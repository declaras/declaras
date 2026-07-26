from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict

from declaras.api import almacen
from declaras.caso import CasoTributario
from declaras.extraccion import extraer_220
from declaras.motor import Elecciones, Flag
from declaras.optimizador import optimizar
from declaras.parametros import ParametrosAnio
from declaras.parametros import cargar as cargar_parametros
from declaras.render import borrador_html, casillas, memoria_markdown

# Debajo de esto la extracción se muestra, pero se marca para revisión humana: el número
# igual entra a un formulario tributario.
CONFIANZA_MINIMA = 0.7

app = FastAPI(title="declaras — demo", version="0.1.0")


class CasoCreado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str


class RespuestaLiquidacion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elecciones: Elecciones
    combos_evaluados: int
    impuesto_neto: int
    saldo: int
    casillas: list[dict]
    flags: list[Flag]


class RespuestaUpload220(BaseModel):
    """El caso completo + una advertencia opcional sobre la extracción.

    La forma es la misma con confianza alta o baja (`advertencia: null`): un front que
    solo lee `caso` no tiene que ramificar, y el aviso no se puede pasar por alto por
    haber aparecido de la nada en un campo que a veces existe.
    """

    model_config = ConfigDict(extra="forbid")

    caso: CasoTributario
    advertencia: str | None = None


def _caso(caso_id: str) -> CasoTributario:
    try:
        return almacen.cargar(caso_id)
    except KeyError:
        raise HTTPException(404, f"Caso {caso_id} no existe") from None
    except ValueError as e:
        # Caso ilegible en disco: dato malo del almacén, no una falla interna.
        raise HTTPException(422, str(e)) from e


def _parametros(caso: CasoTributario) -> ParametrosAnio:
    try:
        return cargar_parametros(caso.anio_gravable)
    except ValueError as e:
        # El año lo elige quien crea el caso: 422, no 500.
        raise HTTPException(422, str(e)) from e


@app.post("/casos", status_code=201)
def crear_caso(caso: CasoTributario) -> CasoCreado:
    return CasoCreado(id=almacen.guardar(caso))


@app.get("/casos/{caso_id}")
def leer_caso(caso_id: str) -> CasoTributario:
    return _caso(caso_id)


@app.put("/casos/{caso_id}")
def reemplazar_caso(caso_id: str, caso: CasoTributario) -> CasoTributario:
    _caso(caso_id)  # 404 si no existe
    almacen.reemplazar(caso_id, caso)
    return caso


@app.post("/casos/{caso_id}/liquidar")
def liquidar_caso(caso_id: str) -> RespuestaLiquidacion:
    caso = _caso(caso_id)
    r = optimizar(caso, _parametros(caso))
    return RespuestaLiquidacion(
        elecciones=r.elecciones,
        combos_evaluados=r.evaluadas,
        impuesto_neto=r.liquidacion.valor("IMPUESTO_NETO"),
        saldo=r.liquidacion.valor("SALDO"),
        casillas=casillas(r.liquidacion),
        flags=r.liquidacion.flags,
    )


@app.get("/casos/{caso_id}/borrador", response_class=HTMLResponse)
def borrador(caso_id: str) -> str:
    caso = _caso(caso_id)
    r = optimizar(caso, _parametros(caso))
    return borrador_html(r.liquidacion, caso)


@app.get("/casos/{caso_id}/memoria", response_class=PlainTextResponse)
def memoria(caso_id: str) -> str:
    caso = _caso(caso_id)
    r = optimizar(caso, _parametros(caso))
    return memoria_markdown(r.liquidacion, caso)


@app.post("/casos/{caso_id}/documentos/220")
def subir_220(caso_id: str, archivo: UploadFile) -> RespuestaUpload220:
    caso = _caso(caso_id)
    pdf = archivo.file.read()
    try:
        # El año del caso es el testigo: el error más común es subir el 220 de otro año.
        laboral = extraer_220(pdf, anio_esperado=caso.anio_gravable)
    except ValueError as e:
        # Todo lo que reporta el extractor es un problema del documento subido (no
        # reconcilia, año equivocado, pensiones, varios certificados, no es PDF).
        raise HTTPException(422, str(e)) from e

    almacen.guardar_documento(laboral.fuente.ref, pdf)
    caso.laborales.append(laboral)
    almacen.reemplazar(caso_id, caso)

    confianza = laboral.fuente.confianza
    advertencia = None
    if confianza is not None and confianza < CONFIANZA_MINIMA:
        advertencia = f"confianza baja ({confianza}): revisar manualmente los valores"
    return RespuestaUpload220(caso=caso, advertencia=advertencia)
