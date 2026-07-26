"""Calibrador de postbacks: pulsa un boton del portal y describe lo que responde.

Reusa el modulo de autenticacion y las utilidades JSF del paquete, de modo que lo que se
calibra aqui es exactamente el camino que corre en produccion.

    uv run python -m scripts.calibrar_postback exogena
    uv run python -m scripts.calibrar_postback documentos --pasos 2
"""

from __future__ import annotations

import argparse
import asyncio
import re

import httpx

from declaras.adapters.dian.endpoints import DASHBOARD_FORM, ENDPOINTS, USER_AGENT
from declaras.adapters.dian.rest import jsf
from declaras.adapters.dian.rest.auth import authenticate
from declaras.config import get_settings
from declaras.domain.models import DianCredentials, IdDocumentKind
from scripts.explorar import load_secrets_file

BOTONES = {
    "rut": DASHBOARD_FORM.rut_copy,
    "exogena": DASHBOARD_FORM.exogena,
    "facturas": DASHBOARD_FORM.einvoices,
    "form210": DASHBOARD_FORM.form_210,
    "obligaciones": DASHBOARD_FORM.obligations,
}


def describir(html: str, *, maximo: int = 14) -> None:
    """Imprime la estructura util de una pagina JSF."""
    titulo = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    print(f"    titulo: {(titulo.group(1).strip()[:70] if titulo else '(sin titulo)')!r}")

    formularios = re.findall(r'<form[^>]*(?:id|name)="([^"]+)"', html, re.I)
    if formularios:
        print(f"    formularios: {formularios[:6]}")

    selects = re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S | re.I)
    for nombre, cuerpo in selects[:6]:
        opciones = re.findall(
            r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', cuerpo, re.S | re.I
        )
        limpias = [f"{v}|{re.sub(r'<[^>]+>', '', t).strip()[:22]}" for v, t in opciones[:8]]
        print(f"    SELECT {nombre}")
        print(f"        opciones: {limpias}")

    visibles = [
        tag
        for tag in re.findall(r"<input[^>]*>", html, re.I)
        if not re.search(r'type\s*=\s*["\']hidden', tag, re.I)
    ]
    print(f"    inputs visibles: {len(visibles)}")
    for tag in visibles[:maximo]:
        tipo = re.search(r'type\s*=\s*["\']([^"\']+)', tag)
        nombre = re.search(r'name\s*=\s*["\']([^"\']+)', tag)
        valor = re.search(r'value\s*=\s*["\']([^"\']*)', tag)
        if nombre:
            etiqueta = f"  value={valor.group(1)[:28]!r}" if valor else ""
            print(f"      [{tipo.group(1) if tipo else '?'}] {nombre.group(1)}{etiqueta}")

    enlaces = re.findall(r'<a[^>]*id="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I)
    utiles = [
        (i, re.sub(r"<[^>]+>", "", t).replace("\xa0", " ").strip()[:40])
        for i, t in enlaces
        if re.sub(r"<[^>]+>", "", t).strip()
    ]
    if utiles:
        print(f"    enlaces con id: {len(utiles)}")
        for i, t in utiles[:10]:
            print(f"      {i}  ->  {t!r}")

    tablas = re.findall(r"<table[^>]*>(.*?)</table>", html, re.S | re.I)
    for cuerpo in tablas[:4]:
        encabezados = [
            re.sub(r"<[^>]+>", "", h).strip()[:22]
            for h in re.findall(r"<th[^>]*>(.*?)</th>", cuerpo, re.S | re.I)
        ]
        filas = len(re.findall(r"<tr[^>]*>", cuerpo, re.I))
        if encabezados:
            print(f"    TABLA filas={filas} columnas={encabezados[:7]}")


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    secrets = load_secrets_file()
    credentials = DianCredentials(
        id_kind=IdDocumentKind.CC,
        id_number=secrets["DIAN_CC"],
        password=secrets["DIAN_PASSWORD"],
    )
    base = settings.dian_base_url.rstrip("/")
    dashboard_url = f"{base}{ENDPOINTS.dashboard}"
    boton = BOTONES[args.boton]

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=90, headers={"User-Agent": USER_AGENT}
    ) as client:
        await authenticate(client, base_url=base, credentials=credentials)
        print(f"  login OK. Pulsando {args.boton} ({boton})\n")

        html = (await client.get(dashboard_url)).text
        payload = jsf.build_postback(html, form_id=DASHBOARD_FORM.form_id, button_id=boton)
        print(f"  postback con {len(payload)} campos")
        response = await client.post(
            dashboard_url,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": base,
                "Referer": dashboard_url,
            },
        )
        tipo = response.headers.get("content-type", "")
        print(f"  -> HTTP {response.status_code}  content-type: {tipo}")
        print(f"  url final: {str(response.url)[:110]}")
        print(f"  bytes: {len(response.content)}")

        if jsf.looks_like_pdf(response.content):
            nombre = jsf.filename_from_disposition(
                response.headers.get("content-disposition"), "documento.pdf"
            )
            print(f"\n  >>> DOCUMENTO DIRECTO: {nombre}")
            return 0

        print("\n  estructura de la respuesta:")
        describir(response.text)

        destino = f"var/postback-{args.boton}.html"
        with open(destino, "w") as fh:
            fh.write(response.text)
        print(f"\n  html guardado en {destino}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("boton", choices=sorted(BOTONES), help="boton del dashboard a pulsar")
    parser.add_argument("--pasos", type=int, default=1, help="reservado para flujos multipaso")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
