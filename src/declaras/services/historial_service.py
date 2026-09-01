"""El historial de declaraciones: que años declaro esta persona, y que años no.

═══ POR QUE ES UN SERVICIO Y NO UN DOCUMENTO MAS DE LA EXTRACCION ═══

La extraccion baja los insumos del calculo del año en curso: el RUT, la exogena, el borrador
sugerido y la declaracion del año ANTERIOR (que aporta patrimonio inicial, anticipos y saldos
a favor). Esa lista es deliberadamente corta porque cada documento es una peticion al portal y
la DIAN bloquea la cuenta a los pocos intentos.

El historial es otra cosa: no lo necesita el motor, lo necesita una persona. Sirve para ver la
vida tributaria del contribuyente, para comparar contra lo que hizo un contador en años pasados
y —lo mas valioso— para detectar el año que FALTA en la mitad de la serie, que es una
declaracion atrasada y por lo tanto un servicio que se puede vender.

═══ EL HUECO ES EL DATO ═══

La DIAN responde con el listado completo de años presentados, asi que los años que NO estan en
ese listado son los que no declaro. Eso no se deduce: se observa. Y por eso el resultado
distingue tres cosas que se ven distinto en la pantalla:

    presentada + guardada    la tenemos, se puede abrir
    presentada, sin traer    la DIAN la tiene y todavia no se ha bajado
    sin declaracion          la DIAN no la tiene: puede ser un atraso

═══ QUE NO HACE ═══

No decide si el año faltante era obligatorio. Que alguien no haya declarado 2024 no significa
que debiera: pudo no superar ningun tope. Eso lo dice el motor con la exogena de ese año, y
mezclarlo aca convertiria una observacion en una acusacion.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import SecretStr

from declaras.domain.case import CaseDocumentSource
from declaras.domain.case_ports import CaseRepository
from declaras.domain.errors import CaseNotFoundError, DianError
from declaras.domain.models import DianCredentials, TaxpayerRef
from declaras.domain.ports import DianConnector, DocumentStore, LoginAttemptGuard
from declaras.observability import get_logger
from declaras.services.apertura import abrir_sesion_con_freno

log = get_logger(__name__)

# Hasta donde mirar atras. Cinco años cubre el periodo en que la DIAN todavia puede revisar o
# sancionar una declaracion (firmeza ordinaria de tres años, mas el margen de las que se
# presentan tarde), y es el tramo en el que un atraso sigue siendo cobrable.
ANIOS_ATRAS = 5

# Como se llama en el expediente la declaracion de un año del historial. Lleva el año DENTRO del
# tipo a proposito: el expediente reemplaza documentos del mismo tipo cuando llega uno nuevo, asi
# que si todas se llamaran igual, cada descarga borraria la anterior y quedaria una sola.
def tipo_de(anio: int) -> str:
    return f"DECLARACION_{anio}"


class HistorialService:
    def __init__(
        self,
        *,
        cases: CaseRepository,
        connector: DianConnector,
        store: DocumentStore,
        guard: LoginAttemptGuard,
    ) -> None:
        self._cases = cases
        self._connector = connector
        self._store = store
        self._guard = guard

    async def ver(self, case_id: UUID) -> list[dict[str, object]]:
        """El historial con lo que ya esta en el expediente. NO abre sesion en el portal.

        Se puede mirar sin la clave, que es lo que hace falta para que la pantalla muestre algo
        desde el primer momento en vez de exigir una consulta antes de decir nada.
        """
        detail = await self._require_detail(case_id)
        return self._armar(detail, anios_en_la_dian=None)

    async def traer(self, case_id: UUID, *, password: SecretStr) -> list[dict[str, object]]:
        """Pregunta a la DIAN que años tiene y baja los que falten.

        UNA SOLA SESION para todo el historial: abrirla es lo caro (y lo que la DIAN cuenta
        para bloquear), asi que el listado y todas las descargas van adentro.
        """
        detail = await self._require_detail(case_id)
        titular = TaxpayerRef(
            id_kind=detail.client.id_kind,
            id_number=detail.client.id_number,
            tax_year=detail.case.tax_year,
        )
        credentials = DianCredentials(
            id_kind=detail.client.id_kind,
            id_number=detail.client.id_number,
            password=password,
        )

        # Con freno: la DIAN bloquea la cuenta al tercer intento fallido, y revisar el
        # historial es la clase de accion que se repite sin pensarla.
        session = await abrir_sesion_con_freno(
            connector=self._connector,
            guard=self._guard,
            credentials=credentials,
            titular=titular,
            motivo="historial",
        )
        try:
            if session.pending_challenge is not None:
                raise DianError(
                    "El portal pidió verificación de identidad. Corre primero una extracción "
                    "(que sí sabe relevar el reto) y vuelve a intentar."
                )
            declaraciones = await session.listar_declaraciones()
            anios_en_la_dian = sorted(
                {int(d["anio"]) for d in declaraciones},  # type: ignore[call-overload]
                reverse=True,
            )
            ya_estan = {
                doc.doc_type for doc in detail.documents if doc.doc_type.startswith("DECLARACION_")
            }
            for anio in self._ventana(detail.case.tax_year):
                if anio not in anios_en_la_dian or tipo_de(anio) in ya_estan:
                    continue
                # Una descarga que falla no cancela las demas: el historial parcial sirve, y
                # que un año concreto no se pueda bajar no dice nada de los otros.
                try:
                    raw = await session.descargar_declaracion(anio)
                except DianError as exc:
                    log.warning(
                        "historial.anio_fallido",
                        case_id=str(case_id),
                        anio=anio,
                        code=exc.code,
                    )
                    continue
                stored = await self._store.put(taxpayer=titular, document=raw, scope_id=case_id)
                await self._cases.add_document(
                    case_id=case_id,
                    doc_type=tipo_de(anio),
                    source=CaseDocumentSource.DIAN_PORTAL,
                    storage_uri=stored.storage_uri,
                    filename=raw.filename,
                    content_sha256=stored.sha256,
                )
        finally:
            await session.close()

        sin_declarar = [a for a in self._ventana(detail.case.tax_year) if a not in anios_en_la_dian]
        cuantas = len(anios_en_la_dian)
        # El año que FALTA se nombra en la bitacora, no solo se cuenta: es el dato sobre el que
        # hay que hacer algo, y un conteo no dice cual fue.
        faltantes = (
            f" y no tiene la de {', '.join(str(a) for a in sin_declarar)}." if sin_declarar else "."
        )
        await self._cases.add_event(
            case_id=case_id,
            kind="DIAN_QUERY",
            message=(
                f"Se revisó el historial: la DIAN tiene {cuantas} "
                f"{'declaración' if cuantas == 1 else 'declaraciones'} presentadas{faltantes}"
            ),
            payload={"anios": anios_en_la_dian, "sin_declarar": sin_declarar},
        )
        log.info(
            "historial.revisado",
            case_id=str(case_id),
            anios=anios_en_la_dian,
            sin_declarar=sin_declarar,
        )
        # Se relee el expediente para que el resultado incluya lo que se acabo de guardar.
        return self._armar(await self._require_detail(case_id), anios_en_la_dian=anios_en_la_dian)

    # ─────────────────────────── internos ───────────────────────────

    def _ventana(self, anio_del_caso: int) -> list[int]:
        """Los años del historial: hacia atras desde el anterior al del expediente.

        El año del expediente no entra: ese no es historial, es el trabajo en curso.
        """
        return [anio_del_caso - n for n in range(1, ANIOS_ATRAS + 1)]

    def _armar(
        self, detail: object, *, anios_en_la_dian: list[int] | None
    ) -> list[dict[str, object]]:
        """Un año por fila, con lo que se sabe de cada uno.

        `anios_en_la_dian` es None cuando no se ha preguntado al portal en esta llamada: ahi el
        estado de un año sin documento es "no se sabe", que es distinto de "no declaro". Decir
        "no declaró" sin haber preguntado seria inventar.
        """
        guardadas = {
            doc.doc_type: doc
            for doc in detail.documents  # type: ignore[attr-defined]
            if doc.doc_type.startswith("DECLARACION_")
        }
        # La del año anterior tambien es historial, aunque la traiga la extraccion con otro
        # nombre: para quien mira la pantalla es la declaracion de ese año y punto.
        anterior = detail.case.tax_year - 1  # type: ignore[attr-defined]
        prior = next(
            (d for d in detail.documents if d.doc_type == "PRIOR_RETURN"),  # type: ignore[attr-defined]
            None,
        )

        filas: list[dict[str, object]] = []
        for anio in self._ventana(detail.case.tax_year):  # type: ignore[attr-defined]
            doc = guardadas.get(tipo_de(anio)) or (prior if anio == anterior else None)
            if doc is not None:
                estado = "guardada"
            elif anios_en_la_dian is None:
                estado = "sin_revisar"
            elif anio in anios_en_la_dian:
                estado = "en_la_dian"
            else:
                estado = "sin_declaracion"
            filas.append(
                {
                    "anio": anio,
                    "estado": estado,
                    "document_id": str(doc.id) if doc else None,
                    "filename": doc.filename if doc else None,
                }
            )
        return filas

    async def _require_detail(self, case_id: UUID):  # type: ignore[no-untyped-def]
        detail = await self._cases.get_detail(case_id)
        if detail is None:
            raise CaseNotFoundError(case_id=str(case_id))
        return detail
