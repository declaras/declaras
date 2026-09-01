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

    guardada         la tenemos, se puede abrir
    sin declaracion  la DIAN no la tiene: puede ser un atraso
    sin revisar      mas atras de lo que trae la consulta, asi que no se sabe

═══ QUE NO HACE ═══

No decide si el año faltante era obligatorio. Que alguien no haya declarado 2024 no significa
que debiera: pudo no superar ningun tope. Eso lo dice el motor con la exogena de ese año, y
mezclarlo aca convertiria una observacion en una acusacion.
"""

from __future__ import annotations

from uuid import UUID

from declaras.domain.case_ports import CaseRepository
from declaras.domain.errors import CaseNotFoundError
from declaras.observability import get_logger

log = get_logger(__name__)

# Cuantos años se MUESTRAN. Es mayor que los que trae la consulta a proposito: los que sobran
# salen como "sin revisar", que es la forma de decir "aca no sabemos" sin fingir que no existen.
# Cinco cubre el periodo en que la DIAN todavia puede revisar o sancionar una declaracion, que
# es el tramo en el que un atraso sigue siendo cobrable.
ANIOS_ATRAS = 5

# Como se llama en el expediente la declaracion de un año del historial. Lleva el año DENTRO del
# tipo a proposito: el expediente reemplaza documentos del mismo tipo cuando llega uno nuevo, asi
# que si todas se llamaran igual, cada descarga borraria la anterior y quedaria una sola.
def tipo_de(anio: int) -> str:
    return f"DECLARACION_{anio}"


class HistorialService:
    def __init__(self, *, cases: CaseRepository) -> None:
        self._cases = cases

    async def ver(self, case_id: UUID) -> list[dict[str, object]]:
        """El historial, leido del expediente. NO abre sesion ni pide la clave.

        HUBO UN "TRAER" Y SE FUE, y vale la pena decir por que: cuando esto se construyo, la
        consulta a la DIAN no traia el historial, asi que habia un boton aparte que pedia la
        clave por segunda vez para bajarlo. En cuanto la consulta empezo a traerlo, ese boton
        quedo pidiendo una clave que ya no hacia falta, para un trabajo que ya estaba hecho.
        Ahora esto solo lee: lo que hay llego con la consulta.
        """
        detail = await self._require_detail(case_id)
        return self._armar(detail)

    # ─────────────────────────── internos ───────────────────────────

    def _ventana(self, anio_del_caso: int) -> list[int]:
        """Los años del historial: hacia atras desde el anterior al del expediente.

        El año del expediente no entra: ese no es historial, es el trabajo en curso.
        """
        return [anio_del_caso - n for n in range(1, ANIOS_ATRAS + 1)]

    def _armar(self, detail: object) -> list[dict[str, object]]:
        """Un año por fila, con lo que se sabe de cada uno.

        ═══ COMO SE SABE QUE UN AÑO NO SE DECLARO, SIN VOLVER A PREGUNTAR ═══

        La consulta trae las declaraciones MAS RECIENTES que la DIAN tenga. Entonces, si hay
        una de 2022 pero no hay de 2023, no es que falte por traer: es que la DIAN no la tiene,
        porque 2023 se habria traido antes que 2022. El hueco entre las que si estan es
        informacion, no ausencia de informacion.

        Mas atras del año mas viejo que tenemos ya no se puede afirmar nada: ahi el limite es
        cuantas trae la consulta, no cuantas existen. Esos años quedan "sin revisar", que es lo
        honesto. Decir "no declaró" sin poder saberlo seria inventar un dato sobre la vida
        tributaria de alguien.
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

        # El año mas viejo que si tenemos marca hasta donde alcanza lo que sabemos.
        anios_con_documento = [
            anio
            for anio in self._ventana(detail.case.tax_year)  # type: ignore[attr-defined]
            if guardadas.get(tipo_de(anio)) or (prior if anio == anterior else None)
        ]
        hasta_donde_sabemos = min(anios_con_documento) if anios_con_documento else None

        filas: list[dict[str, object]] = []
        for anio in self._ventana(detail.case.tax_year):  # type: ignore[attr-defined]
            doc = guardadas.get(tipo_de(anio)) or (prior if anio == anterior else None)
            if doc is not None:
                estado = "guardada"
            elif hasta_donde_sabemos is not None and anio > hasta_donde_sabemos:
                estado = "sin_declaracion"
            else:
                estado = "sin_revisar"
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
