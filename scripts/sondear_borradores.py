"""Sonda de SOLO LECTURA sobre la API del formulario 210 de la DIAN.

POR QUE EXISTE: escribir el 210 en el portal y dejarlo guardado como borrador es el ultimo tramo
del producto y lo unico que hoy hace el contador a mano. La API que maneja esos borradores YA la
usamos para leer (`renta210ingreso`), asi que antes de capturar trafico del navegador vale la pena
preguntarle a la propia API que sabe: que anios ofrece, que borradores hay, y que forma tiene el
recurso. De ahi sale que hace falta descubrir y que no.

NO ESCRIBE NADA. Solo GET. Crear o guardar un borrador toca la cuenta real de un contribuyente y
eso se hace aparte, a conciencia y con la persona enterada.

    uv run python scripts/sondear_borradores.py
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from declaras.adapters.dian.endpoints import DIAN_API
from declaras.adapters.dian.rest.connector import HttpDianConnector
from declaras.config import get_settings
from declaras.domain.models import DianCredentials, IdDocumentKind, TaxpayerRef
from scripts.explorar import load_secrets_file

ANIO = 2025


def _forma(valor: Any, nivel: int = 0) -> str:
    """La FORMA del json, no su contenido: nombres de campos y tipos, sin cifras de nadie."""
    sangria = "  " * nivel
    if isinstance(valor, dict):
        return "\n".join(f"{sangria}{k}: {_forma(v, nivel + 1).lstrip()}" for k, v in valor.items())
    if isinstance(valor, list):
        if not valor:
            return f"{sangria}[] (vacio)"
        return f"{sangria}[{len(valor)} items] ->\n{_forma(valor[0], nivel + 1)}"
    return f"{sangria}{type(valor).__name__}"


async def main() -> int:
    secrets = load_secrets_file()
    settings = get_settings()
    credenciales = DianCredentials(
        id_kind=IdDocumentKind.CC,
        id_number=secrets["DIAN_CC"],
        password=secrets["DIAN_PASSWORD"],
    )
    titular = TaxpayerRef(id_number=secrets["DIAN_CC"], tax_year=ANIO)

    conector = HttpDianConnector(base_url=settings.dian_base_url)
    sesion = await conector.open_session(credenciales, titular)
    api = sesion._ctx.api

    consultas = [
        ("años disponibles", DIAN_API.renta_years),
        ("versiones del 210", DIAN_API.renta_form_versions),
        ("borradores (pendiente)", f"{DIAN_API.renta_forms}?estado={DIAN_API.state_pending}"),
        ("presentadas", f"{DIAN_API.renta_forms}?estado={DIAN_API.state_filed}"),
        ("borradores en la API del formato", DIAN_API.renta_form_v18),
    ]
    for nombre, ruta in consultas:
        print(f"\n{'=' * 70}\n{nombre}\n  GET {ruta}")
        try:
            payload = await api.get_json(ruta)
        except Exception as exc:
            print(f"  -> {type(exc).__name__}: {exc}")
            continue
        print(_forma(payload))
        crudo = json.dumps(payload, ensure_ascii=False)
        if len(crudo) < 1500:
            print(f"  crudo: {crudo}")

    # QUE VERBOS ADMITE CADA RECURSO. `OPTIONS` pregunta, no modifica: es la forma de descubrir
    # como se escribe sin escribir. Lo que respondio el 2026-08-19:
    #   /formularios       -> GET, POST   (crear un borrador)
    #   /formularios/{id}  -> GET, PUT    (guardar sus casillas)
    print(f"\n{'=' * 70}\nverbos que admite cada recurso")
    if api._bearer is None:
        await api.authenticate()
    borradores = await api.get_json(DIAN_API.renta_form_v18)
    primero = (borradores.get("infoFormularios") or [{}])[0].get("identificador", {}).get("id")
    rutas = [DIAN_API.renta_form_v18]
    if primero:
        rutas.append(DIAN_API.renta_form_v18_one.format(form_id=primero))
    for ruta in rutas:
        r = await api._client.options(
            f"{DIAN_API.base_url}{ruta}",
            headers=api._headers(),
        )
        print(f"  {ruta}\n     Allow: {r.headers.get('allow')}")

    await sesion.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
