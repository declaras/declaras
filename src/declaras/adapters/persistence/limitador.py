"""Cuantas veces puede pedir un mismo origen, por ventana de tiempo.

═══ QUE PROTEGE, QUE NO ES LO MISMO QUE EL FRENO DE LOGIN ═══

El freno de login cuenta por CEDULA y protege la cuenta del contribuyente: que nadie le queme
los tres intentos a una persona. Esto cuenta por ORIGEN y protege otra cosa: la IP de este
servicio.

La consulta publica hace que nuestro servidor entre al portal de la DIAN. Alguien que la llame
en bucle con cedulas distintas —cada una con sus dos intentos disponibles— nos convierte en el
que golpea el portal miles de veces, y el que termina bloqueado por la DIAN es nuestro
despliegue, con todos los clientes adentro. El freno por cedula no lo impide: cada cedula
cumple su cuota.

═══ VENTANA FIJA, Y POR QUE ALCANZA ═══

Se cuenta por hora de reloj, no en una ventana deslizante. La ventana fija tiene un borde
conocido: entre el minuto 59 y el 01 se puede hacer el doble del limite. Para lo que esto
defiende —que nadie sostenga miles de peticiones— ese factor de dos no cambia nada, y una
ventana deslizante cuesta guardar cada marca de tiempo en vez de un contador.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from declaras.adapters.persistence.tables import PeticionesPorOrigenRow
from declaras.domain.errors import DeclarasError
from declaras.observability import get_logger

log = get_logger(__name__)


class DemasiadasPeticionesError(DeclarasError):
    """429 y no 403: no es que al origen le falte permiso, es que pidio demasiado seguido."""

    code = "DEMASIADAS_PETICIONES"
    http_status = 429
    default_message = (
        "Demasiadas consultas seguidas desde este punto de acceso. Espera un rato y vuelve a "
        "intentar."
    )
    # Reintentar SI sirve, pasada la ventana. Es lo que distingue esto de un error del cliente.
    retryable = True


class LimitadorPorOrigen:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def registrar(self, *, origen: str, recurso: str, limite: int) -> None:
        """Suma una peticion y lanza 429 si el origen ya paso su cuota de la hora.

        SUMA PRIMERO Y PREGUNTA DESPUES, a proposito: si preguntara antes de sumar, dos
        peticiones simultaneas leerian el mismo conteo y las dos pasarian. Con el incremento
        atomico como primer paso, el numero que se compara ya incluye esta peticion.
        """
        ventana = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        clave = f"{origen}|{recurso}|{ventana.isoformat()}"

        async with self._sessions() as session, session.begin():
            hecho = await session.execute(
                update(PeticionesPorOrigenRow)
                .where(PeticionesPorOrigenRow.clave == clave)
                .values(conteo=PeticionesPorOrigenRow.conteo + 1)
            )
            if hecho.rowcount == 0:
                session.add(
                    PeticionesPorOrigenRow(clave=clave, conteo=1, ventana_inicio=ventana)
                )
                await session.flush()
                conteo = 1
            else:
                conteo = await session.scalar(
                    select(PeticionesPorOrigenRow.conteo).where(
                        PeticionesPorOrigenRow.clave == clave
                    )
                ) or 1

        if conteo > limite:
            log.warning(
                "limite.excedido",
                recurso=recurso,
                conteo=conteo,
                limite=limite,
                # El origen NO se registra completo: es un dato personal (una IP identifica a
                # alguien) y para saber que hubo abuso basta el conteo. Si hace falta rastrear,
                # el identificador de peticion de la plataforma lo permite.
                origen_parcial=origen[:7],
            )
            raise DemasiadasPeticionesError()

    async def limpiar(self, *, antes_de: datetime) -> int:
        """Borra ventanas viejas. Sin esto la tabla crece una fila por origen y por hora, para
        siempre, y nada la volveria a leer."""
        async with self._sessions() as session, session.begin():
            filas = await session.execute(
                select(func.count())
                .select_from(PeticionesPorOrigenRow)
                .where(PeticionesPorOrigenRow.ventana_inicio < antes_de)
            )
            cuantas = filas.scalar() or 0
            if cuantas:
                await session.execute(
                    PeticionesPorOrigenRow.__table__.delete().where(
                        PeticionesPorOrigenRow.ventana_inicio < antes_de
                    )
                )
        return cuantas
