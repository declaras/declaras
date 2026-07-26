"""Explorador post-login: entra con TUS credenciales y mapea el portal por dentro.

Para que existe: el login ya esta calibrado, pero las paginas internas (RUT, exogena,
declaraciones, facturacion) solo se pueden inspeccionar con una sesion real. Este script
entra, se queda quieto, y guarda el mapa de lo que ve (menus, enlaces, rutas) para poder
calibrar los descargadores sin adivinar.

NO descarga nada ni modifica nada: solo mira y toma capturas.

    uv run python scripts/explorar.py --cc TU_CEDULA

La clave se pide por teclado y no queda en el historial. Tambien se puede dejar en un
archivo local que git ignora (.secrets.env con DIAN_CC y DIAN_PASSWORD), para que otra
persona ejecute la calibracion sin que la clave pase por un chat.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import Page, async_playwright

from declaras.adapters.dian.flows.login import perform_login
from declaras.adapters.dian.selectors import SELECTORS
from declaras.config import get_settings
from declaras.domain.errors import DeclarasError
from declaras.domain.models import DianCredentials, IdDocumentKind

OUT_DIR = Path("var/exploracion")

MAP_JS = """
() => ({
  url: location.href,
  title: document.title,
  links: Array.from(document.querySelectorAll('a')).slice(0, 120).map(a => ({
    text: (a.innerText || '').trim().slice(0, 60),
    href: (a.getAttribute('href') || '').slice(0, 140),
    id: a.id || '',
  })).filter(a => a.text || a.href),
  buttons: Array.from(document.querySelectorAll('button')).slice(0, 60).map(b => ({
    text: (b.innerText || '').trim().slice(0, 60), id: b.id || '',
  })).filter(b => b.text),
  menus: Array.from(document.querySelectorAll(
    "[class*='menu'], [class*='nav'], mat-tree, mat-list, ul"
  )).slice(0, 12).map(m => ({
    cls: (m.className || '').toString().slice(0, 60),
    text: (m.innerText || '').trim().slice(0, 400),
  })),
  frames: Array.from(document.querySelectorAll('iframe, frame')).map(f => ({
    id: f.id || '', src: (f.getAttribute('src') || '').slice(0, 140),
  })),
})
"""


async def snapshot(page: Page, label: str) -> dict:
    data = await page.evaluate(MAP_JS)
    data["label"] = label
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(OUT_DIR / f"{label}.png"), full_page=True)
    print(f"    [{label}] {data['url'][:80]}")
    print(f"      enlaces: {len(data['links'])}  botones: {len(data['buttons'])}")
    return data


def load_secrets_file(path: Path = Path(".secrets.env")) -> dict[str, str]:
    """Lee credenciales de un archivo local que git ignora. Devuelve {} si no existe.

    Existe para que otra persona (o un agente) pueda ejecutar la calibracion sin que la
    clave viaje por un chat o quede en el historial de la terminal.
    """
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    secrets = load_secrets_file()

    cedula = args.cc or secrets.get("DIAN_CC") or os.environ.get("DIAN_CC")
    if not cedula:
        print("  falta la cedula: usa --cc o define DIAN_CC en .secrets.env")
        return 2
    args.cc = cedula

    password = (
        args.password
        or secrets.get("DIAN_PASSWORD")
        or os.environ.get("DIAN_PASSWORD")
        or getpass.getpass("  clave de la DIAN (no se muestra): ")
    )
    if not password:
        print("  falta la clave: definela en .secrets.env o dejala vacia para escribirla aqui")
        return 2
    credentials = DianCredentials(id_kind=IdDocumentKind.CC, id_number=cedula, password=password)

    print(f"\n  portal: {settings.dian_base_url}")
    print("  ATENCION: esto SI hace login real y consume un intento.\n")

    report: dict = {"generated_at": datetime.now(UTC).isoformat(), "pages": []}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=args.headless, slow_mo=250)
        page = await browser.new_page(locale="es-CO", viewport={"width": 1440, "height": 900})
        try:
            outcome = await perform_login(
                page,
                base_url=settings.dian_base_url,
                credentials=credentials,
                challenge_ttl_s=settings.dian_challenge_ttl_s,
            )
        except DeclarasError as exc:
            print(f"  LOGIN FALLO [{exc.code}] {exc.message}")
            print(f"  detalles: {exc.details}")
            await snapshot(page, "login-fallo")
            await browser.close()
            return 1

        if outcome.challenge is not None:
            print(f"  El portal pidio verificacion: {outcome.challenge.prompt}")
            await snapshot(page, "reto-identidad")
            if sys.stdin.isatty():
                input("  responde en el navegador y presiona ENTER: ")
            else:
                # Ejecucion no interactiva: se espera a que el usuario resuelva el reto
                # en la ventana del navegador, sondeando el marcador de autenticado.
                print(f"  Responde en la ventana del navegador. Espero {args.wait_challenge}s...")
                marker = SELECTORS.login.authenticated_marker
                for _ in range(args.wait_challenge):
                    await page.wait_for_timeout(1_000)
                    try:
                        if await page.locator(marker).first.is_visible(timeout=500):
                            print("  verificacion resuelta")
                            break
                    except Exception:
                        continue
                else:
                    print("  no se completo la verificacion; se mapea lo que haya")

        print("  LOGIN OK. Mapeando el portal por dentro:\n")
        report["pages"].append(await snapshot(page, "00-dashboard"))

        # Los enlaces del dashboard son la fuente de verdad de las rutas internas.
        dash_links = report["pages"][-1].get("links", [])
        print(f"\n  enlaces del dashboard: {len(dash_links)}")

        for label, path in [
            ("01-rut", "/WebRutMuisca/DefConsultaEstadoRUT.faces"),
            ("02-exogena", "/WebInformacionExogena/DefConsultaInformacionExogena.faces"),
            ("03-documentos", "/WebArquitectura/DefConsultaDocumentos.faces"),
            (
                "04-factura-electronica",
                "/WebFacturaElectronica/DefConsultaDocumentosRecibidos.faces",
            ),
        ]:
            try:
                await page.goto(
                    f"{settings.dian_base_url.rstrip('/')}{path}",
                    wait_until="domcontentloaded",
                    timeout=45_000,
                )
                await page.wait_for_timeout(2_500)
                report["pages"].append(await snapshot(page, label))
            except Exception as exc:
                print(f"    [{label}] no se pudo abrir: {type(exc).__name__}")
                report["pages"].append({"label": label, "error": str(exc)[:200]})

        out = OUT_DIR / "mapa.json"
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"\n  mapa guardado en {out}")
        print(f"  capturas en {OUT_DIR}/\n")

        if not args.headless:
            print("  (navegador abierto 30 segundos por si quieres navegar a mano)")
            await page.wait_for_timeout(30_000)
        await browser.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default=None, help="tu cedula (o DIAN_CC en .secrets.env)")
    parser.add_argument("--password", default=None, help="mejor dejarlo vacio: se pide por teclado")
    parser.add_argument("--headless", action="store_true", help="sin ventana (por defecto se ve)")
    parser.add_argument(
        "--wait-challenge", type=int, default=120, help="segundos de espera si hay reto"
    )
    raise SystemExit(asyncio.run(main(parser.parse_args())))
