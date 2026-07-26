"""Ensayo del login contra el portal REAL, sin enviar el formulario.

Usa exactamente el mismo codigo que la extraccion real (`fill_login_form`), pero se
detiene justo antes de pulsar "Ingresar". Sirve para verificar que los selectores estan
bien calibrados sin gastar ninguno de los intentos que la DIAN permite antes de
bloquear la cuenta.

    uv run python scripts/ensayo_login.py                 # datos ficticios, headless
    uv run python scripts/ensayo_login.py --ver           # abre el navegador para mirar
    uv run python scripts/ensayo_login.py --cc 123456789  # con tu cedula (sin clave real)
"""

from __future__ import annotations

import argparse
import asyncio

from playwright.async_api import async_playwright

from declaras.adapters.dian.flows.login import fill_login_form
from declaras.config import get_settings
from declaras.domain.errors import DeclarasError
from declaras.domain.models import DianCredentials, IdDocumentKind


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    credentials = DianCredentials(
        id_kind=IdDocumentKind.CC,
        id_number=args.cc,
        password=args.password,
    )
    print(f"\n  portal: {settings.dian_base_url}")
    print(f"  cedula de ensayo: {args.cc}   (el formulario NO se envia)\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.ver)
        page = await browser.new_page(locale="es-CO", viewport={"width": 1440, "height": 900})
        try:
            await fill_login_form(page, base_url=settings.dian_base_url, credentials=credentials)
        except DeclarasError as exc:
            print(f"  FALLO [{exc.code}] {exc.message}")
            print(f"  detalles: {exc.details}")
            await page.screenshot(path="var/ensayo-login-fallo.png", full_page=True)
            print("  captura: var/ensayo-login-fallo.png\n")
            await browser.close()
            return 1

        await page.screenshot(path="var/ensayo-login-ok.png", full_page=True)
        print("  OK: el portal acepto el formulario y habilito el boton Ingresar.")
        print("  Eso confirma que los selectores del login estan calibrados.")
        print("  captura: var/ensayo-login-ok.png\n")
        if args.ver:
            print("  (navegador abierto 15 segundos para que lo revises)")
            await page.wait_for_timeout(15_000)
        await browser.close()
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="1111111111", help="numero de documento de ensayo")
    parser.add_argument("--password", default="ensayo-no-real", help="clave de ensayo")
    parser.add_argument("--ver", action="store_true", help="mostrar el navegador")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
