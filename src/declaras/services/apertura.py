"""Abrir sesion en el portal SIN quemar la cuenta del contribuyente.

═══ EL DANO QUE ESTO EVITA ═══

La DIAN bloquea la cuenta al TERCER intento fallido, y desbloquearla no es un boton: es un
tramite. Una cuenta bloqueada en temporada de vencimientos es una persona que no puede declarar
y a la que le empieza a correr sancion. O sea que un contador de fallos mal puesto no produce
un error de sistema, produce un dano a un tercero.

El flujo de extraccion siempre tuvo ese freno. El problema es que despues aparecieron TRES
caminos mas que abren sesion —la consulta publica de "¿me toca declarar?", escribir el borrador
en el portal y traer el historial— y ninguno paso por el, porque el freno vivia dentro del
servicio de extraccion en vez de vivir junto a la operacion que hay que frenar.

El caso peor era la consulta publica: no exige autenticacion, asi que cualquiera en internet
podia mandar tres peticiones con la cedula de otra persona (dato que en Colombia esta en
cualquier factura) y claves inventadas, y dejarle la cuenta de la DIAN bloqueada. Un curl en
bucle, sin ser cliente, sin dejar rastro de quien fue.

═══ POR QUE ES UNA FUNCION Y NO UNA REGLA ESCRITA EN LA DOCUMENTACION ═══

Porque la proxima operacion que necesite una sesion tambien va a olvidarla. Aca la unica forma
de abrir sesion es pasando por el freno: no hay que acordarse.
"""

from __future__ import annotations

from declaras.domain.errors import DianInvalidCredentialsError
from declaras.domain.models import DianCredentials, TaxpayerRef
from declaras.domain.ports import DianConnector, DianSession, LoginAttemptGuard
from declaras.observability import get_logger

log = get_logger(__name__)


async def abrir_sesion_con_freno(
    *,
    connector: DianConnector,
    guard: LoginAttemptGuard,
    credentials: DianCredentials,
    titular: TaxpayerRef,
    motivo: str,
) -> DianSession:
    """Abre sesion contando los fallos por contribuyente, y corta antes del bloqueo.

    `motivo` solo va al registro: sirve para saber cual de los caminos gasto el intento cuando
    haya que explicarle a alguien por que su cuenta quedo frenada.

    El contador se lleva por SUJETO (la cedula), no por sesion ni por IP, porque lo que la DIAN
    bloquea es la cuenta: es al titular a quien hay que proteger, sin importar de donde vino el
    intento.
    """
    subject = titular.subject_key
    await guard.assert_can_attempt(subject)
    try:
        session = await connector.open_session(credentials, titular)
    except DianInvalidCredentialsError as exc:
        restantes = await guard.register_failure(subject)
        log.warning("dian.login.fallo", motivo=motivo, intentos_restantes=restantes)
        raise DianInvalidCredentialsError(
            exc.message, attempts_remaining=restantes, **exc.details
        ) from exc
    # Un ingreso bueno limpia la cuenta: los fallos que importan son los CONSECUTIVOS, que es
    # como los cuenta la DIAN. Si no se limpiara, dos claves mal escritas a lo largo de un mes
    # dejarian al contribuyente a un intento del bloqueo para siempre.
    await guard.reset(subject)
    return session
