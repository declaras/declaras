"""Calibrador de selectores: inspecciona el portal real SIN credenciales.

Abre la pagina de login del Muisca (que es publica), lista los campos reales del
formulario y compara con lo que tenemos en selectors.py. Sirve para ajustar los
selectores antes de arriesgar un intento de login con una clave real.

    uv run python scripts/calibrar.py [url]
"""

from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright

from declaras.adapters.dian.selectors import SELECTORS

DEFAULT_URL = "https://muisca.dian.gov.co/WebArquitectura/DefLogin.faces"

DUMP_JS = """
() => {
  const describe = (el) => ({
    tag: el.tagName.toLowerCase(),
    type: el.getAttribute('type') || '',
    id: el.id || '',
    name: el.getAttribute('name') || '',
    placeholder: el.getAttribute('placeholder') || '',
    value: (el.getAttribute('value') || '').slice(0, 40),
    options: el.tagName.toLowerCase() === 'select'
      ? Array.from(el.options).slice(0, 12).map(o => `${o.value}|${o.text.trim()}`)
      : [],
  });
  return {
    title: document.title,
    url: location.href,
    forms: Array.from(document.forms).map(f => ({ id: f.id, action: f.action })),
    fields: Array.from(document.querySelectorAll('input, select, textarea')).map(describe),
    buttons: Array.from(document.querySelectorAll(
      "button, input[type=submit], input[type=button], a[onclick]"
    )).slice(0, 15).map(b => ({
      tag: b.tagName.toLowerCase(),
      id: b.id || '',
      text: (b.innerText || b.value || '').trim().slice(0, 40),
    })),
    frames: Array.from(document.querySelectorAll('iframe, frame')).map(f => ({
      id: f.id || '', src: (f.getAttribute('src') || '').slice(0, 80),
    })),
  };
}
"""


async def main(url: str) -> int:
    print(f"\n  Inspeccionando: {url}\n" + "  " + "─" * 68)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(locale="es-CO")
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            print(f"  ERROR de navegacion: {type(exc).__name__}: {str(exc)[:160]}")
            await browser.close()
            return 1

        status = response.status if response else "?"
        info = await page.evaluate(DUMP_JS)

        print(f"  HTTP {status}   titulo: {info['title'][:60]}")
        print(f"  url final: {info['url'][:90]}\n")

        if info["frames"]:
            print("  FRAMES (el formulario puede vivir adentro):")
            for fr in info["frames"]:
                print(f"    id={fr['id']!r} src={fr['src']}")
            print()

        print(f"  CAMPOS ({len(info['fields'])}):")
        for f in info["fields"]:
            if f["type"] == "hidden":
                continue
            line = f"    {f['tag']}[{f['type']}] id={f['id']!r} name={f['name']!r}"
            if f["placeholder"]:
                line += f" ph={f['placeholder']!r}"
            print(line)
            for opt in f["options"]:
                print(f"        opcion: {opt}")

        print(f"\n  BOTONES ({len(info['buttons'])}):")
        for b in info["buttons"]:
            print(f"    {b['tag']} id={b['id']!r} texto={b['text']!r}")

        print("\n  VERIFICACION DE NUESTROS SELECTORES:")
        checks = {
            "login.id_kind": SELECTORS.login.id_kind,
            "login.id_number": SELECTORS.login.id_number,
            "login.password": SELECTORS.login.password,
            "login.submit": SELECTORS.login.submit,
            "login.account_scope": SELECTORS.login.account_scope,
        }
        misses = 0
        for name, selector in checks.items():
            try:
                count = await page.locator(selector).count()
            except Exception:
                count = -1
            mark = "OK   " if count > 0 else "FALLA"
            if count <= 0:
                misses += 1
            print(f"    [{mark}] {name}: {count} coincidencia(s)  <-- {selector[:60]}")

        await page.screenshot(path="var/calibracion-login.png", full_page=True)
        print("\n  captura guardada en var/calibracion-login.png")
        await browser.close()

        if misses:
            print(f"\n  >>> {misses} selector(es) hay que ajustar en adapters/dian/selectors.py\n")
        else:
            print("\n  >>> todos los selectores del login coinciden\n")
        return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    raise SystemExit(asyncio.run(main(target)))
