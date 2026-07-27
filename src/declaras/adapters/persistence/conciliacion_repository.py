"""Persistencia del conciliador: partidas, respuestas y versiones de la liquidacion.

Mismo patron que `case_repository.py`: una sesion por operacion, el dominio entra y sale
como modelos pydantic, y las filas nunca se filtran hacia arriba.

LAS PARTIDAS SE GUARDAN COMPLETAS Y SE REEMPLAZAN EN BLOQUE. Completas porque las cuatro
marcas estructurales del cruce (`reportado_a`, `versiones_documento`, `version_que_rige`,
`documentos_por_cruzar`) son lo que distingue "esta plata es de otra persona" de una nota
de texto que la siguiente capa reescribe; guardar un resumen dejaria esa marca fuera y el
ingreso de un tercero entraria al caso del contribuyente. En bloque porque la lista es el
resultado de una re-derivacion completa del cruce: mezclar filas nuevas con filas viejas
que "sobrevivieron" es exactamente como se cuelan partidas fantasma con ids que ya no
existen, y `a_caso` cuenta dos veces cualquier id repetido.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from declaras.adapters.persistence.tables import (
    CaseLiquidacionRow,
    CasePartidaRow,
    CaseRespuestaRow,
)
from declaras.services.conciliacion import LiquidacionVersionada, Partida, Respuesta


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class SqlConciliacionRepository:
    """Implementa `ConciliacionRepository`."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    # ─────────────────────────── partidas ───────────────────────────

    async def guardadas(self, case_id: UUID) -> list[Partida]:
        """Todas las partidas persistidas, las vivas y las huerfanas.

        Las dos juntas porque las dos son `guardadas` para `refrescar`: una huerfana cuyo
        id reaparece con las mismas cifras recupera su resolucion, que es justo lo que se
        quiere cuando la DIAN republica el reporte con la fila de vuelta.
        """
        async with self._sessions() as session:
            filas = (
                await session.execute(
                    select(CasePartidaRow)
                    .where(CasePartidaRow.case_id == str(case_id))
                    .order_by(CasePartidaRow.partida_id)
                )
            ).scalars()
            return [Partida.model_validate(f.partida_json) for f in filas]

    async def vivas(self, case_id: UUID) -> list[Partida]:
        return await self._partidas(case_id, sin_partida=False)

    async def huerfanas(self, case_id: UUID) -> list[Partida]:
        return await self._partidas(case_id, sin_partida=True)

    async def reemplazar_partidas(
        self, case_id: UUID, *, partidas: list[Partida], huerfanas: list[Partida]
    ) -> None:
        """Deja EXACTAMENTE estas partidas y estas huerfanas, en una sola transaccion."""
        ahora = _utcnow()
        async with self._sessions() as session, session.begin():
            await session.execute(
                delete(CasePartidaRow).where(CasePartidaRow.case_id == str(case_id))
            )
            for sin_partida, grupo in ((False, partidas), (True, huerfanas)):
                for partida in grupo:
                    session.add(
                        CasePartidaRow(
                            id=str(uuid4()),
                            case_id=str(case_id),
                            partida_id=partida.id,
                            estado=partida.estado.value,
                            sin_partida=sin_partida,
                            partida_json=partida.model_dump(mode="json"),
                            updated_at=ahora,
                        )
                    )

    async def _partidas(self, case_id: UUID, *, sin_partida: bool) -> list[Partida]:
        async with self._sessions() as session:
            filas = (
                await session.execute(
                    select(CasePartidaRow)
                    .where(
                        CasePartidaRow.case_id == str(case_id),
                        CasePartidaRow.sin_partida.is_(sin_partida),
                    )
                    .order_by(CasePartidaRow.partida_id)
                )
            ).scalars()
            return [Partida.model_validate(f.partida_json) for f in filas]

    # ─────────────────────────── respuestas ───────────────────────────

    async def respuestas(self, case_id: UUID) -> list[Respuesta]:
        async with self._sessions() as session:
            filas = (
                await session.execute(
                    select(CaseRespuestaRow)
                    .where(CaseRespuestaRow.case_id == str(case_id))
                    .order_by(CaseRespuestaRow.pregunta)
                )
            ).scalars()
            return [Respuesta.model_validate(f.respuesta_json) for f in filas]

    async def registrar_respuesta(self, case_id: UUID, respuesta: Respuesta) -> Respuesta:
        """La ultima respuesta a una pregunta reemplaza la anterior.

        El cliente puede corregirse ("dije que no y sí tengo prepagada"), y guardar las dos
        dejaria al derivador de peticiones eligiendo entre respuestas contradictorias por
        orden de llegada.
        """
        async with self._sessions() as session, session.begin():
            fila = (
                await session.execute(
                    select(CaseRespuestaRow).where(
                        CaseRespuestaRow.case_id == str(case_id),
                        CaseRespuestaRow.pregunta == respuesta.pregunta,
                    )
                )
            ).scalar_one_or_none()
            if fila is None:
                fila = CaseRespuestaRow(
                    id=str(uuid4()), case_id=str(case_id), pregunta=respuesta.pregunta
                )
                session.add(fila)
            fila.tiene = respuesta.tiene
            fila.respuesta_json = respuesta.model_dump(mode="json")
            fila.updated_at = _utcnow()
            await session.flush()
        return respuesta

    # ─────────────────────────── liquidaciones ───────────────────────────

    async def versiones(self, case_id: UUID) -> list[LiquidacionVersionada]:
        """Todas las versiones, de la primera a la ultima. La primera es el preliminar."""
        async with self._sessions() as session:
            filas = (
                await session.execute(
                    select(CaseLiquidacionRow)
                    .where(CaseLiquidacionRow.case_id == str(case_id))
                    .order_by(CaseLiquidacionRow.version)
                )
            ).scalars()
            return [
                LiquidacionVersionada.model_validate(
                    {
                        "version": f.version,
                        "momento": _as_utc(f.momento),
                        "liquidacion": f.liquidacion_json,
                    }
                )
                for f in filas
            ]

    async def agregar_version(
        self, case_id: UUID, version: LiquidacionVersionada
    ) -> LiquidacionVersionada:
        async with self._sessions() as session, session.begin():
            session.add(
                CaseLiquidacionRow(
                    id=str(uuid4()),
                    case_id=str(case_id),
                    version=version.version,
                    momento=version.momento,
                    impuesto=version.impuesto,
                    saldo=version.saldo,
                    liquidacion_json=version.liquidacion.model_dump(mode="json"),
                )
            )
        return version
