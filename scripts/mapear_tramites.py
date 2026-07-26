"""Mapea los tramites internos del Muisca haciendo clic desde el dashboard.

Por que existe: el portal es JSF y sus enlaces son href="#" con submit por JavaScript,
asi que no se puede navegar por URL. Hay que entrar al dashboard y hacer clic. Este
script hace eso para cada tramite de solo lectura que nos interesa y guarda la
estructura real de cada pagina (URL final, campos, botones, tablas) para calibrar los
descargadores.

DISCIPLINA: solo navega y toma capturas. No llena formularios, no envia nada, no
descarga y no toca el flujo de "Diligenciar y presentar" (eso es fase 2).

    uv run python scripts/mapear_tramites.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import Page, async_playwright

from declaras.adapters.dian.flows.login import perform_login
from declaras.adapters.dian.selectors import SELECTORS
from declaras.config import get_settings
from declaras.domain.errors import DeclarasError
from declaras.domain.models import DianCredentials, IdDocumentKind
from scripts.explorar import load_secrets_file

OUT = Path("var/tramites")

# Solo consultas de lectura, por el id del icono del dashboard (que es lo clickeable).
D = SELECTORS.dashboard
TRAMITES = [
    ("rut", D.rut_copy),
    ("exogena", D.exogena),
    ("facturas", D.einvoices),
    ("obligaciones", D.obligations),
    ("recibos", D.payment_receipts),
]

INSPECT_JS = """
() => {
  const vis = (el) => el.offsetParent !== null;
  return {
    url: location.href,
    title: document.title,
    heading: (document.querySelector('h1,h2,legend,.titulo')?.innerText || '').trim().slice(0,80),
    fields: Array.from(document.querySelectorAll('input,select,textarea')).filter(vis).map(el => ({
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      id: el.id || '',
      name: el.getAttribute('name') || '',
      options: el.tagName.toLowerCase() === 'select'
        ? Array.from(el.options).slice(0,10).map(o => `${o.value}|${o.text.trim()}`) : [],
    })),
    buttons: Array.from(document.querySelectorAll(
      "input[type=submit],input[type=button],button,a[onclick]"
    )).filter(vis).slice(0,25).map(b => ({
      id: b.id || '', name: b.getAttribute('name') || '',
      text: (b.innerText || b.value || '').trim().slice(0,45),
    })),
    tables: Array.from(document.querySelectorAll('table')).slice(0,6).map(t => ({
      id: t.id || '', rows: t.rows.length,
      head: Array.from(t.querySelectorAll('th')).slice(0,10).map(h => h.innerText.trim().slice(0,30)),
    })),
    frames: Array.from(document.querySelectorAll('iframe,frame')).map(f => ({
      id: f.id || '', name: f.getAttribute('name') || '', src: (f.getAttribute('src')||'').slice(0,120),
    })),
  };
}
"""


async def inspect(page: Page, label: str) -> dict:
    await page.wait_for_timeout(2_500)
    data = await page.evaluate(INSPECT_JS)
    OUT.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(OUT / f"{label}.png"), full_page=True)
    print(f"    url: {data['url'][:95]}")
    print(f"    titulo: {data['title'][:50]!r}  encabezado: {data['heading'][:50]!r}")
    if data["frames"]:
        print(f"    FRAMES: {[f['name'] or f['id'] for f in data['frames']]}")
    for f in data["fields"][:8]:
        extra = f"  opciones={f['options'][:4]}" if f["options"] else ""
        print(f"      campo {f['tag']}[{f['type']}] id={f['id']!r} name={f['name']!r}{extra}")
    for b in data["buttons"][:6]:
        print(f"      boton id={b['id']!r} texto={b['text']!r}")
    for t in data["tables"]:
        if t["rows"] > 1:
            print(f"      tabla id={t['id']!r} filas={t['rows']} cols={t['head'][:5]}")
    return data


async def main() -> int:
    settings = get_settings()
    secrets = load_secrets_file()
    credentials = DianCredentials(
        id_kind=IdDocumentKind.CC,
        id_number=secrets["DIAN_CC"],
        password=secrets["DIAN_PASSWORD"],
    )
    dashboard = f"{settings.dian_base_url.rstrip('/')}{SELECTORS.login.dashboard_path}"
    report: dict = {"generated_at": datetime.now(UTC).isoformat(), "tramites": {}}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="es-CO", accept_downloads=True, viewport={"width": 1440, "height": 1000}
        )
        page = await context.new_page()
        page.set_default_timeout(45_000)

        try:
            await perform_login(
                page,
                base_url=settings.dian_base_url,
                credentials=credentials,
                challenge_ttl_s=settings.dian_challenge_ttl_s,
            )
        except DeclarasError as exc:
            print(f"  LOGIN FALLO [{exc.code}] {exc.message}")
            await browser.close()
            return 1
        print("  login OK\n")
        report["tramites"]["_dashboard"] = await inspect(page, "00-dashboard")

        # Handlers registrados una sola vez: Playwright necesita funciones propias.
        nuevas: list = []
        descargas: list = []

        def _on_new_page(nueva: object) -> None:
            nuevas.append(nueva)

        def _on_download(descarga: object) -> None:
            descargas.append(descarga)

        context.on("page", _on_new_page)
        page.on("download", _on_download)

        for label, selector in TRAMITES:
            print(f"\n  >>> {label}: clic en {selector}")
            try:
                await page.goto(dashboard, wait_until="domcontentloaded")
                await page.wait_for_timeout(1_800)
                url_antes = page.url

                # El portal puede responder de tres formas: navegar en la misma pagina,
                # abrir una pestana nueva, o disparar una descarga directa.
                nuevas.clear()
                descargas.clear()

                await page.locator(selector).first.click(timeout=20_000)
                await page.wait_for_timeout(6_000)

                if descargas:
                    d = descargas[0]
                    nombre = d.suggested_filename
                    print(f"    DESCARGA DIRECTA: {nombre}")
                    report["tramites"][label] = {
                        "mecanismo": "descarga_directa",
                        "filename": nombre,
                        "url": d.url[:200],
                    }
                    continue

                if nuevas:
                    popup = nuevas[-1]
                    await popup.wait_for_load_state("domcontentloaded")
                    print("    PESTANA NUEVA:")
                    data = await inspect(popup, label)
                    data["mecanismo"] = "pestana_nueva"
                    report["tramites"][label] = data
                    await popup.close()
                    continue

                if page.url == url_antes:
                    print("    sin efecto visible (misma URL, sin popup ni descarga)")
                    report["tramites"][label] = {"mecanismo": "sin_efecto", "url": page.url}
                    continue

                data = await inspect(page, label)
                data["mecanismo"] = "navegacion"
                report["tramites"][label] = data
            except Exception as exc:
                print(f"    NO SE PUDO: {type(exc).__name__}: {str(exc)[:120]}")
                report["tramites"][label] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                with contextlib.suppress(Exception):
                    await page.screenshot(path=str(OUT / f"{label}-error.png"), full_page=True)

        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "tramites.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"\n  mapa guardado en {OUT / 'tramites.json'}")
        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
