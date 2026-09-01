"""Las consultas de "¿me toca declarar?": el embudo antes del expediente.

Quien llega aca todavia no es cliente y puede que nunca lo sea. Por eso NO abre un expediente
—eso arrastra documentos, cruce y liquidacion para alguien que solo pregunto— sino una fila
propia con lo minimo: quien es, por donde pregunto, que contesto y en que quedo.

═══ EL VEREDICTO SE CALCULA ACA, NO SE RECIBE ═══

El front manda las RESPUESTAS y el servidor decide. Recibir el veredicto ya cocinado dejaria la
regla de la obligacion en dos sitios —el navegador y el motor— y bastaria abrir las herramientas
del navegador para guardarse un "no obligado" que nadie calculo. Ademas es la misma regla del
art. 592 que ya usa el motor, con sus comparadores asimetricos.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from declaras.domain.errors import ValidationError
from declaras.observability import get_logger
from declaras.services.apertura import abrir_sesion_con_freno
from declaras.services.cifrado import cifrar
from declaras.tax.obligation import (
    THRESHOLD_LABELS,
    THRESHOLD_LIMITS_IN_UVT,
    ThresholdCode,
)

log = get_logger(__name__)

# Lo que puede contestar alguien a cada tope.
RESPUESTAS_VALIDAS = frozenset({"si", "no", "no-se"})


def veredicto_de(respuestas: dict[str, str]) -> str:
    """OBLIGADO, NO_OBLIGADO o NO_CONCLUYENTE, con la regla del art. 592.

    BASTA UN SOLO "SI". Los cinco topes son alternativos, no acumulativos: superar uno obliga,
    y por eso el front puede cortar la consulta en la primera afirmativa sin cambiar el
    resultado. Aca se comprueba igual sobre lo que haya llegado.

    UNA DUDA NO ES UN "NO". Sin ningun "si" pero con algun "no estoy seguro", el resultado es que
    NO SE PUEDE SABER: tratar la duda como negativa seria decirle a alguien que no declare sobre
    una pregunta que no contesto, y quien deja de declarar debiendo se entera con la sancion.
    """
    valores = set(respuestas.values())
    if "si" in valores:
        return "OBLIGADO"
    # Las cinco tienen que estar contestadas para poder concluir que no.
    contestados = {c for c in respuestas if c in {t.value for t in ThresholdCode}}
    if "no-se" in valores or len(contestados) < len(THRESHOLD_LIMITS_IN_UVT):
        return "NO_CONCLUYENTE"
    return "NO_OBLIGADO"


class ConsultasService:
    def __init__(
        self,
        *,
        repo: object,
        clave_de_cifrado: str | None,
        connector: object | None = None,
        guard: object | None = None,
    ) -> None:
        self._repo = repo
        self._clave = clave_de_cifrado
        self._connector = connector
        self._guard = guard

    async def consultar_con_la_dian(
        self,
        *,
        nombre: str,
        correo: str,
        whatsapp: str,
        id_number: str,
        dian_password: str,
        tax_year: int,
    ) -> dict[str, object]:
        """La respuesta EXACTA: los cinco topes que la DIAN ya tiene reportados.

        ═══ POR QUE ESTO NO ES EL CUESTIONARIO CON OTRA CARA ═══

        El cuestionario compara contra lo que la persona RECUERDA, y quien recuerda por debajo
        se entera de su error con la sancion. Esto compara contra lo que los bancos y los
        empleadores ya le reportaron a la DIAN, que es la misma cifra con la que la DIAN
        decide. Por eso el resultado puede decir cuanto y de donde, no solo si.

        No abre expediente ni descarga nada mas que la exogena: quien pregunta si le toca
        declarar todavia no es cliente, y bajarle cinco documentos seria cobrarle una
        extraccion completa a alguien que solo pregunto.
        """
        from declaras.documents.parsers import exogena as parser_exogena
        from declaras.domain.models import (
            DianCredentials,
            DocumentType,
            IdDocumentKind,
            TaxpayerRef,
        )
        from declaras.tax.obligation import ThresholdCode, assess

        if self._connector is None:
            raise ValidationError("La consulta con la DIAN no está disponible en este despliegue.")
        if self._guard is None:
            # Sin freno NO se consulta, y la negativa es deliberada. Este endpoint es publico:
            # sin contador de intentos, cualquiera puede bloquear la cuenta de la DIAN de un
            # tercero con tres peticiones y una cedula ajena. Es el lado correcto en el que
            # fallar: mejor sin funcion que con una que le hace dano a quien no pregunto.
            raise ValidationError("La consulta con la DIAN no está disponible en este despliegue.")

        titular = TaxpayerRef(id_number=id_number, tax_year=tax_year)
        sesion = await abrir_sesion_con_freno(
            connector=self._connector,  # type: ignore[arg-type]
            guard=self._guard,  # type: ignore[arg-type]
            credentials=DianCredentials(
                id_kind=IdDocumentKind.CC, id_number=id_number, password=dian_password
            ),
            titular=titular,
            motivo="consulta_publica",
        )
        try:
            documento = await sesion.download(DocumentType.EXOGENA, titular)
        finally:
            await sesion.close()

        lectura = parser_exogena.parse(documento.content)
        reportado: dict[ThresholdCode, int] = {}
        for campo in lectura.fields:
            if not campo.name.startswith("tope_"):
                continue
            try:
                codigo = ThresholdCode(campo.name.removeprefix("tope_"))
            except ValueError:
                continue
            reportado[codigo] = int(campo.value or 0)

        veredicto = assess(tax_year=tax_year, reported=reportado)
        resultado = "OBLIGADO" if veredicto.is_obligated else "NO_OBLIGADO"

        await self.registrar(
            nombre=nombre,
            correo=correo,
            whatsapp=whatsapp,
            via="dian",
            id_number=id_number,
            dian_password=dian_password,
        )
        # Los topes viajan enteros: el que obliga y los que no. Mostrar solo el que obliga deja a
        # la persona sin saber por cuanto paso ni que tan cerca estuvo de los otros.
        return {
            "resultado": resultado,
            "anio": tax_year,
            "topes": [
                {
                    "codigo": t.code.value,
                    "nombre": THRESHOLD_LABELS[t.code],
                    "reportado": t.reported_amount,
                    "limite": t.limit_amount,
                    "supera": t.exceeded,
                }
                for t in veredicto.thresholds
            ],
        }

    async def registrar(
        self,
        *,
        nombre: str,
        correo: str,
        whatsapp: str,
        via: str,
        respuestas: dict[str, str] | None = None,
        id_number: str | None = None,
        dian_password: str | None = None,
    ) -> tuple[UUID, str | None]:
        """Guarda la consulta y devuelve su id con el veredicto, si se pudo calcular."""
        respuestas = respuestas or {}
        invalidas = {k: v for k, v in respuestas.items() if v not in RESPUESTAS_VALIDAS}
        if invalidas:
            raise ValidationError(
                f"Respuestas que no se reconocen: {sorted(invalidas)}. "
                f"Cada tope se contesta con una de {sorted(RESPUESTAS_VALIDAS)}."
            )

        resultado = veredicto_de(respuestas) if respuestas else None
        # La clave se cifra ANTES de salir de este metodo: ninguna capa de abajo la ve en claro,
        # y el repositorio recibe un texto que no sirve sin la llave del despliegue.
        cifrada = cifrar(dian_password, llave=self._clave) if dian_password else None

        consulta_id = uuid4()
        await self._repo.guardar_consulta(  # type: ignore[attr-defined]
            consulta_id=consulta_id,
            nombre=nombre.strip(),
            correo=correo.strip().lower(),
            whatsapp="".join(c for c in whatsapp if c.isdigit()),
            via=via,
            respuestas=respuestas,
            resultado=resultado,
            id_number=id_number,
            dian_password_cifrada=cifrada,
            cuando=datetime.now(UTC),
        )
        log.info(
            "consulta.registrada",
            consulta_id=str(consulta_id),
            via=via,
            resultado=resultado,
            con_clave=bool(cifrada),
        )
        return consulta_id, resultado
