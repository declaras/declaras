"""Escribir el 210 calculado en el portal de la DIAN, con las compuertas del producto.

El adaptador sabe COMO escribir (la API, la codificacion, la relectura); este servicio
decide CUANDO se puede y deja el rastro. Son responsabilidades distintas a proposito: el
adaptador no conoce expedientes ni estados, y este servicio no conoce la forma del payload.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import SecretStr

from declaras.domain.case import CaseStatus
from declaras.domain.case_ports import CaseRepository
from declaras.domain.errors import CaseNotFoundError, DeclarasError, DianError
from declaras.domain.models import BorradorEscrito, DianCredentials, TaxpayerRef
from declaras.domain.ports import DianConnector
from declaras.observability import get_logger
from declaras.services.conciliacion_service import ConciliacionService

log = get_logger(__name__)


class BorradorNoCerradoError(DeclarasError):
    """El borrador no esta dado por listo: escribirlo seria publicar cifras sin revisar.

    Es la misma disciplina del cierre, extendida al portal: lo que sale de Clara hacia la
    cuenta real de un contribuyente tiene que haber pasado por el "dar por buena" del
    contador. Un 210 a medio decidir en el Muisca es peor que ninguno, porque nada en el
    portal dice que esta incompleto.
    """

    code = "BORRADOR_NO_CERRADO"
    http_status = 409
    default_message = (
        "El borrador tiene que estar dado por listo antes de escribirlo en el portal. "
        "Revisa y cierra el borrador primero."
    )


class EscrituraService:
    def __init__(
        self,
        *,
        connector: DianConnector,
        cases: CaseRepository,
        conciliacion: ConciliacionService,
    ) -> None:
        self._connector = connector
        self._cases = cases
        self._conciliacion = conciliacion

    async def escribir(self, case_id: UUID, password: SecretStr) -> BorradorEscrito:
        """Lleva el 210 del expediente al borrador del portal y verifica lo guardado.

        LA CLAVE NO SE PERSISTE. Llega en la peticion, abre la sesion y se suelta: el mismo
        trato que le da la extraccion, por la misma razon — la clave del portal es del
        contribuyente, no un dato del expediente. La cedula NO viaja en la peticion: sale
        del expediente, porque aceptar otra seria escribir el 210 de una persona con la
        sesion de otra.
        """
        detail = await self._cases.get_detail(case_id)
        if detail is None:
            raise CaseNotFoundError(case_id=str(case_id))
        if detail.case.status is not CaseStatus.DRAFT_READY:
            raise BorradorNoCerradoError()
        credentials = DianCredentials(
            id_kind=detail.client.id_kind,
            id_number=detail.client.id_number,
            password=password,
        )

        # Las casillas se calculan ANTES de abrir la sesion: si el caso no se puede armar,
        # el 409 sale sin haber gastado un login del portal (la DIAN bloquea la cuenta a
        # los pocos intentos, asi que cada sesion cuenta).
        casillas = {c.numero: c.valor for c in await self._conciliacion.formulario(case_id)}

        titular = TaxpayerRef(
            id_kind=detail.client.id_kind,
            id_number=detail.client.id_number,
            tax_year=detail.case.tax_year,
        )
        session = await self._connector.open_session(credentials, titular)
        try:
            if session.pending_challenge is not None:
                # El relevo de identidad vive en el flujo de extraccion. Antes que duplicarlo
                # aca, se le dice a quien opera que la extraccion (que suele correr primero)
                # deja la sesion verificada.
                raise DianError(
                    "El portal pidió verificación de identidad. Corre primero una "
                    "extracción (que sí sabe relevar el reto) y vuelve a intentar."
                )
            resultado = await session.escribir_borrador(titular, casillas)
        finally:
            await session.close()

        veredicto = (
            "verificado renglón por renglón"
            if resultado.verificado
            else "CON DIFERENCIAS al releer"
        )
        await self._cases.add_event(
            case_id=case_id,
            kind="PORTAL_WRITE",
            message=(
                f"Se escribió el borrador {resultado.form_id} en el portal de la DIAN: "
                f"{resultado.escritas} casillas, {veredicto}."
            ),
        )
        log.info(
            "portal.write_done",
            case_id=str(case_id),
            form_id=resultado.form_id,
            verificado=resultado.verificado,
        )
        return resultado
