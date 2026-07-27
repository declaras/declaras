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
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from declaras.adapters.persistence.tables import (
    CaseConciliacionRow,
    CaseLiquidacionRow,
    CasePartidaRow,
    CaseRespuestaRow,
)
from declaras.domain.errors import ConflictoDeConcurrenciaError
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

    async def revision(self, case_id: UUID) -> int:
        """La revision del cruce del expediente; 0 si nunca se concilio."""
        async with self._sessions() as session:
            fila = await session.get(CaseConciliacionRow, str(case_id))
            return fila.revision if fila is not None else 0

    async def huella(self, case_id: UUID) -> str | None:
        """La huella de los documentos con que se derivaron los renglones guardados.

        None SOLO si nunca se concilio, que es un hecho del expediente y no de sus filas: un
        expediente con cero renglones tambien tiene su sello. Es lo que distingue "no hay
        nada que cruzar" de "hay documentos que todavia no se cruzaron".
        """
        async with self._sessions() as session:
            fila = await session.get(CaseConciliacionRow, str(case_id))
            return fila.huella_documentos if fila is not None else None

    async def reemplazar_partidas(
        self,
        case_id: UUID,
        *,
        partidas: list[Partida],
        huerfanas: list[Partida],
        revision_esperada: int,
        huella_documentos: str,
    ) -> int:
        """Deja EXACTAMENTE estas partidas y estas huerfanas, si nadie mas escribio antes.

        `huella_documentos` es OBLIGATORIO y no acepta None. Con un default, un llamador
        futuro que lo olvide borra el sello en silencio y devuelve el agujero que esta ronda
        cerro: falla cerrado (todo se niega hasta conciliar), pero es exactamente el accidente
        que no debe poder escribirse.

        CHEQUEO OPTIMISTA, no bloqueo. La ventana entre que quien llama lee el estado y
        llega aca es ancha (varias consultas y la liquidacion completa en medio), y sin
        precondicion la segunda escritura borraba el bloque de la primera: las dos
        respondian 200 y en la base quedaba una sola decision.

        LA PRECONDICION ES UN UPDATE CONDICIONAL, NO UN SELECT. Medido: con un `SELECT` de
        la revision dentro de la transaccion el defecto seguia reproduciendose 1 de 1, porque
        la segunda transaccion alcanza a leer el estado de ANTES de que la primera confirme y
        pasa el chequeo igual. Un `UPDATE ... WHERE revision = :esperada` es un
        comparar-y-cambiar: toma el candado de escritura y se evalua contra lo ya confirmado,
        asi que si alguien subio la revision no coincide con ninguna fila y se sabe. Va como
        PRIMERA sentencia para que la transaccion nazca escritora y espere el candado en vez
        de fallar al subir de lectora a escritora.

        No se usa `SELECT ... FOR UPDATE`: exigiria hilar una sesion por todo el caso de uso
        y no corre en SQLite, que es lo que usan las pruebas.

        `IntegrityError` se traduce al mismo conflicto: dos PRIMERAS escrituras concurrentes
        (revision 0, cuando todavia no hay fila que comparar) chocan contra la clave primaria
        de `case_conciliacion`, que es el mismo accidente visto desde la base. Sin esta
        traduccion seria un 500 donde corresponde un 409.
        """
        ahora = _utcnow()
        nueva = revision_esperada + 1
        try:
            async with self._sessions() as session, session.begin():
                if revision_esperada:
                    # `execute` esta tipado como `Result`, pero un UPDATE devuelve
                    # `CursorResult`, que es el unico que trae el conteo de filas tocadas
                    # (que es justo la respuesta del comparar-y-cambiar).
                    cambiadas = cast(
                        "CursorResult[Any]",
                        await session.execute(
                            update(CaseConciliacionRow)
                            .where(
                                CaseConciliacionRow.case_id == str(case_id),
                                CaseConciliacionRow.revision == revision_esperada,
                            )
                            .values(
                                revision=nueva,
                                huella_documentos=huella_documentos,
                                updated_at=ahora,
                            )
                        ),
                    )
                    if not cambiadas.rowcount:
                        raise ConflictoDeConcurrenciaError(
                            revision_leida=revision_esperada
                        )
                else:
                    session.add(
                        CaseConciliacionRow(
                            case_id=str(case_id),
                            revision=nueva,
                            huella_documentos=huella_documentos,
                            updated_at=ahora,
                        )
                    )
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
        except IntegrityError as exc:
            raise ConflictoDeConcurrenciaError() from exc
        return nueva

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
                        "base_sin_documentos": f.base_sin_documentos,
                    }
                )
                for f in filas
            ]

    async def agregar_version(
        self, case_id: UUID, version: LiquidacionVersionada
    ) -> LiquidacionVersionada:
        """Agrega una version. Dos inserciones concurrentes del mismo numero chocan contra
        `uq_liquidacion_caso_version`, y eso es un conflicto de concurrencia (409), no una
        falla del servidor: quien llama conto las versiones antes de insertar."""
        try:
            async with self._sessions() as session, session.begin():
                session.add(
                    CaseLiquidacionRow(
                        id=str(uuid4()),
                        case_id=str(case_id),
                        version=version.version,
                        momento=version.momento,
                        impuesto=version.impuesto,
                        saldo=version.saldo,
                        base_sin_documentos=version.base_sin_documentos,
                        liquidacion_json=version.liquidacion.model_dump(mode="json"),
                    )
                )
        except IntegrityError as exc:
            raise ConflictoDeConcurrenciaError(version=version.version) from exc
        return version
