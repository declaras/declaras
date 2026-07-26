"""Inspecciona el trafico de red del Muisca: que hay API y que hay JSF legacy.

Herramienta de calibracion. Muestra las llamadas XHR, los tokens en storage, las cookies
de sesion y como se pide realmente un documento. Incluye un experimento que intenta
replicar la descarga con un cliente HTTP puro reusando las cookies.

Conclusiones de la corrida del 2026-07-25 en docs/adr/0003.

    uv run python -m scripts.inspeccionar_red
"""

import asyncio
import json
from collections import Counter

import httpx
from playwright.async_api import async_playwright

from declaras.adapters.dian.flows.login import perform_login
from declaras.adapters.dian.selectors import SELECTORS
from declaras.config import get_settings
from declaras.domain.models import DianCredentials, IdDocumentKind
from scripts.explorar import load_secrets_file

INTERESANTE = ("json", "javascript")


async def main():
    st = get_settings()
    sec = load_secrets_file()
    creds = DianCredentials(
        id_kind=IdDocumentKind.CC, id_number=sec["DIAN_CC"], password=sec["DIAN_PASSWORD"]
    )
    reqs = []

    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True)
        ctx = await b.new_context(locale="es-CO", accept_downloads=True)
        page = await ctx.new_page()
        page.set_default_timeout(60_000)

        def on_req(r):
            reqs.append(
                {
                    "method": r.method,
                    "url": r.url,
                    "type": r.resource_type,
                    "auth": "authorization" in {k.lower() for k in r.headers},
                    "post_keys": (r.post_data or "")[:150],
                }
            )

        page.on("request", on_req)

        await perform_login(page, base_url=st.dian_base_url, credentials=creds, challenge_ttl_s=600)
        print("login OK\n")

        print("=== PETICIONES XHR/FETCH DURANTE EL LOGIN ===")
        for r in reqs:
            if r["type"] in ("xhr", "fetch"):
                print(f"  {r['method']:5} {r['url'][:110]}")
                if r["auth"]:
                    print("        (lleva header Authorization)")
                if r["post_data" if False else "post_keys"]:
                    print(f"        body: {r['post_keys'][:110]}")

        print("\n=== TIPOS DE RECURSO (conteo) ===")
        for t, n in Counter(r["type"] for r in reqs).most_common():
            print(f"  {t}: {n}")

        # tokens en storage
        print("\n=== TOKENS EN LOCALSTORAGE / SESSIONSTORAGE ===")
        storage = await page.evaluate("""() => ({
            local: Object.fromEntries(Object.entries(localStorage).map(([k,v]) => [k, String(v).slice(0,60)])),
            session: Object.fromEntries(Object.entries(sessionStorage).map(([k,v]) => [k, String(v).slice(0,60)])),
        })""")
        print("  localStorage:", json.dumps(storage["local"], ensure_ascii=False)[:400] or "vacio")
        print(
            "  sessionStorage:", json.dumps(storage["session"], ensure_ascii=False)[:400] or "vacio"
        )

        print("\n=== COOKIES ===")
        cookies = await ctx.cookies()
        for c in cookies:
            print(f"  {c['name']} = {str(c['value'])[:45]}...  (domain={c['domain']})")

        # descarga del RUT: capturar la URL exacta
        print("\n=== DESCARGA DEL RUT: como se pide realmente ===")
        dash = f"{st.dian_base_url}{SELECTORS.login.dashboard_path}"
        await page.goto(dash, wait_until="domcontentloaded")
        await page.wait_for_timeout(2_000)
        reqs.clear()
        async with page.expect_download(timeout=60_000) as dl_info:
            await page.locator(SELECTORS.dashboard.rut_copy).first.click()
        dl = await dl_info.value
        print(f"  archivo: {dl.suggested_filename}")
        print(f"  url de la descarga: {dl.url[:150]}")
        print("  peticiones disparadas por el clic:")
        for r in reqs:
            if r["type"] in ("document", "xhr", "fetch", "other"):
                print(f"    {r['method']:5} {r['url'][:110]}")
                if r["post_keys"]:
                    print(f"          body: {r['post_keys'][:120]}")

        # EXPERIMENTO: reusar las cookies en un cliente HTTP puro
        print("\n=== EXPERIMENTO: pedir el mismo PDF con httpx usando las cookies ===")
        jar = {c["name"]: c["value"] for c in await ctx.cookies()}
        try:
            async with httpx.AsyncClient(cookies=jar, follow_redirects=True, timeout=30) as cli:
                resp = await cli.get(dl.url)
            print(f"  HTTP {resp.status_code}  content-type={resp.headers.get('content-type')}")
            print(f"  bytes: {len(resp.content)}  empieza con: {resp.content[:8]!r}")
            print(
                f"  -> {'PDF VALIDO: la replica HTTP funciona' if resp.content[:4] == b'%PDF' else 'NO es PDF: la replica directa no sirve'}"
            )
        except Exception as e:
            print(f"  fallo: {type(e).__name__}: {str(e)[:120]}")

        await b.close()


asyncio.run(main())
