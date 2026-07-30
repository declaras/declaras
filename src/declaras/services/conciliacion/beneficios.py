"""De las lecturas de certificados de beneficio al `Beneficios` que el motor liquida.

Es el eslabón que faltaba del plan. El conciliador cruza INGRESOS: cada partida tiene dos
lados porque la DIAN reportó algo y el contribuyente aporta su versión. Un beneficio no tiene
dos lados — la DIAN no sabe que alguien paga medicina prepagada, y ahí está justamente su
valor: es la plata que no entra al 210 si nadie manda el papel. Así que no pasa por partidas ni
por resoluciones, y necesita su propio camino de la lectura al caso.

LAS DOS DECISIONES QUE NO SON OBVIAS

**Cuándo sumar y cuándo no.** Dos certificados de prepagada de dos aseguradoras distintas son
dos pagos y suman. El MISMO certificado subido dos veces —un re-escaneo, un PDF re-exportado—
llega con otro hash, así que `content_sha256` no lo detecta, y sumarlo dobla una deducción en
silencio. La regla: se identifica el certificado por (tipo, entidad, valor). Igual y es el
mismo; distinto en cualquiera de los tres y son dos. Las dos ramas avisan, porque una cifra
compuesta que el contador no puede descomponer es una cifra que no puede defender.

**Nada se descarta callado.** Un certificado sin soporte formal no entra, pero deja aviso: la
alternativa es que el beneficio desaparezca del 210 y nadie sepa por qué.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from declaras.caso import (
    AporteAfc,
    Beneficios,
    Donacion,
    Fuente,
    MontoDeclarado,
)
from declaras.dinero import en_pesos
from declaras.documents.models import DocumentReading
from declaras.motor.traza import Flag

# Largo del id corto de documento con el que `Fuente.ref` identifica el archivo. La lectura
# guarda el digest completo de 64; los extractores publican este prefijo (`id_documento`).
LARGO_ID_DOCUMENTO = 12

# El `doc_type` del certificado bancario, que es el único que trae un beneficio de rebote: su
# razón de ser es el ingreso por rendimientos, y el 4x1000 viene en el mismo papel.
DOC_TYPE_BANCARIO = "CERT_BANCARIO"

# Qué casilla de `Beneficios` llena cada tipo. Es una partición TOTAL de `TipoBeneficio`: un
# tipo nuevo sin entrada acá revienta, igual que un `Concepto` nuevo revienta en el ensamble
# del caso y en el catálogo de peticiones. La alternativa —ignorarlo— es un beneficio que se
# lee, se cobra la llamada al modelo, y no se declara.
_CASILLA_POR_TIPO: dict[str, str] = {
    "PREPAGADA": "medicina_prepagada",
    "INTERESES_VIVIENDA": "intereses_vivienda",
    "ICETEX": "intereses_icetex",
    "AFC_FVP": "aportes_afc_fvp",
    "DONACION_ESAL": "donaciones_esal",
}

# Las casillas que son una lista y no un monto único: varios aportes o varias donaciones son
# hechos separados en el 210, con su entidad cada uno.
_CASILLAS_DE_LISTA = frozenset({"aportes_afc_fvp", "donaciones_esal"})


def beneficios_de(
    lecturas: Iterable[DocumentReading],
) -> tuple[Beneficios, list[Flag]]:
    """Arma los beneficios del caso con las lecturas del expediente, y lo que hay que avisar.

    Recibe TODAS las lecturas y filtra las que aportan un beneficio: quien llama no tiene por
    qué saber qué `doc_type` es de qué familia.
    """
    encontrados: dict[str, list[_Aporte]] = {}
    avisos: list[Flag] = []

    for lectura in lecturas:
        aporte = _aporte_de(lectura, avisos)
        if aporte is None:
            continue
        encontrados.setdefault(aporte.casilla, []).append(aporte)

    campos: dict[str, Any] = {}
    for casilla, aportes in encontrados.items():
        unicos = _sin_repetidos(aportes, avisos)
        if casilla in _CASILLAS_DE_LISTA:
            campos[casilla] = [a.a_modelo_de_lista() for a in unicos]
        else:
            campos[casilla] = _un_solo_monto(casilla, unicos, avisos)

    return Beneficios(**campos), avisos


class _Aporte:
    """Un beneficio leído de un documento, antes de saber si suma o si es un repetido."""

    def __init__(
        self, *, casilla: str, tipo: str, entidad: str, entidad_nit: str, valor: int, fuente: Fuente
    ) -> None:
        self.casilla = casilla
        self.tipo = tipo
        self.entidad = entidad
        self.entidad_nit = entidad_nit
        self.valor = valor
        self.fuente = fuente

    @property
    def identidad(self) -> tuple[str, str, int]:
        """Qué hace que dos documentos sean el MISMO certificado.

        No es el hash: un re-escaneo del mismo papel llega con otro. Mismo beneficio, misma
        entidad y mismo valor es el mismo certificado con una probabilidad muy alta, y el
        costo de equivocarse en esa dirección (no sumar dos pagos idénticos de la misma
        entidad, cosa rarísima) es mucho menor que el de doblar una deducción callado.
        """
        return (self.tipo, self.entidad_nit, self.valor)

    def a_modelo_de_lista(self) -> Any:
        if self.casilla == "aportes_afc_fvp":
            # El tipo AFC vs FVP cambia el tope, y el certificado no siempre lo distingue con
            # esas palabras. Se asume AFC, que es el más común, y el motor aplica su tope.
            return AporteAfc(entidad=self.entidad, tipo="AFC", valor=self.valor, fuente=self.fuente)
        return Donacion(
            entidad=self.entidad,
            valor=self.valor,
            # Solo llega acá si el certificado es formal: el guard está arriba.
            certificada=True,
            fuente=self.fuente,
        )


def _aporte_de(lectura: DocumentReading, avisos: list[Flag]) -> _Aporte | None:
    """El beneficio que aporta una lectura, o None si no aporta ninguno."""
    if lectura.doc_type == DOC_TYPE_BANCARIO:
        gmf = lectura.field("gmf_pagado") or 0
        if gmf <= 0:
            # El banco no lo reporta. Un `MontoDeclarado(valor=0)` afirmaría que no se pagó
            # 4x1000, y lo que pasa es que el certificado no lo dice.
            return None
        return _Aporte(
            casilla="gmf_pagado",
            tipo="GMF",
            entidad=lectura.field("entidad_nombre") or "",
            entidad_nit=lectura.field("entidad_nit") or "",
            valor=int(gmf),
            fuente=_fuente(lectura),
        )

    tipo = lectura.field("tipo_beneficio")
    if tipo is None:
        return None  # la exógena, el RUT, el 220: no aportan beneficio y no son un problema

    if tipo not in _CASILLA_POR_TIPO:
        # Partición total. Un tipo nuevo sin casilla es un error de quien programa, no del
        # documento, y callarlo dejaría el beneficio fuera del 210 sin rastro.
        raise NotImplementedError(
            f"El beneficio {tipo!r} no tiene casilla en el caso: hay que darle una o "
            "declararlo explícitamente como no declarable."
        )

    if not lectura.field("certificada"):
        # Segunda red: el extractor ya rechaza lo que no es un certificado formal. Si una
        # lectura llega igual, el beneficio no entra —sería el que hay que devolver con
        # intereses si la DIAN revisa— pero se avisa, porque desaparecer callado es peor.
        avisos.append(
            Flag(
                codigo="BENEFICIO_SIN_SOPORTE",
                mensaje=(
                    f"El documento de {lectura.field('entidad_nombre') or 'la entidad'} no es "
                    "un certificado formal, así que ese beneficio no entró a la declaración."
                ),
            )
        )
        return None

    valor = int(lectura.field("valor") or 0)
    if valor <= 0:
        return None

    return _Aporte(
        casilla=_CASILLA_POR_TIPO[tipo],
        tipo=tipo,
        entidad=lectura.field("entidad_nombre") or "",
        entidad_nit=lectura.field("entidad_nit") or "",
        valor=valor,
        fuente=_fuente(lectura),
    )


def _sin_repetidos(aportes: list[_Aporte], avisos: list[Flag]) -> list[_Aporte]:
    """Descarta el mismo certificado subido dos veces, y lo dice."""
    vistos: dict[tuple[str, str, int], _Aporte] = {}
    repetidos = 0
    for aporte in aportes:
        if aporte.identidad in vistos:
            repetidos += 1
            continue
        vistos[aporte.identidad] = aporte
    if repetidos:
        avisos.append(
            Flag(
                codigo="CERTIFICADO_REPETIDO",
                mensaje=(
                    f"Llegó {repetidos} certificado{'s' if repetidos != 1 else ''} repetido"
                    f"{'s' if repetidos != 1 else ''} (misma entidad y mismo valor), y se "
                    "contó una sola vez para no duplicar la deducción."
                ),
                severidad="info",
            )
        )
    return list(vistos.values())


def _un_solo_monto(
    casilla: str, aportes: list[_Aporte], avisos: list[Flag]
) -> MontoDeclarado | None:
    """Una sola cifra para la casilla, sumando si hay varios certificados legítimos."""
    if not aportes:
        return None
    if len(aportes) == 1:
        return MontoDeclarado(valor=aportes[0].valor, fuente=aportes[0].fuente)

    total = sum(a.valor for a in aportes)
    detalle = ", ".join(f"{a.entidad} {en_pesos(a.valor)}" for a in aportes)
    avisos.append(
        Flag(
            codigo="BENEFICIO_DE_VARIOS_CERTIFICADOS",
            mensaje=(
                f"El renglón lleva {en_pesos(total)} sumando {len(aportes)} "
                f"certificados: {detalle}. "
                "Hay que verificar que ninguno esté repetido."
            ),
        )
    )
    # La proveniencia queda en el primero y el aviso lleva el detalle: `MontoDeclarado` tiene
    # una sola fuente, y partirlo es un cambio del modelo del caso, que está congelado.
    return MontoDeclarado(valor=total, fuente=aportes[0].fuente)


def _fuente(lectura: DocumentReading) -> Fuente:
    """La proveniencia del beneficio: el documento del que salió."""
    return Fuente.documento(
        lectura.doc_type.lower(),
        lectura.content_sha256[:LARGO_ID_DOCUMENTO],
        confianza=lectura.fields[0].confidence if lectura.fields else None,
    )
