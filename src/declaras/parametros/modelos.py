from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from declaras.dinero import pesos


class Tramo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    desde_uvt: int
    hasta_uvt: int | None  # None = último tramo, sin tope
    tarifa: float
    # Constante publicada del art. 241, en UVT (116, 788, ...). SIN default a propósito:
    # un tramo que la omite no es "constante 0", es un YAML incompleto, y el default
    # silencioso subestimaba el impuesto de ese tramo en millones.
    constante_uvt: int


class ParametrosAnio(BaseModel):
    # Una clave desconocida revienta en vez de descartarse: si el YAML de un año trae un
    # parámetro que el motor no lee (renombrado, nuevo, con typo), el motor liquidaría
    # con la regla vieja sin decir nada.
    model_config = ConfigDict(extra="forbid")

    anio: int
    uvt: int
    uvt_siguiente: int
    tope_obligacion_ingresos_uvt: int
    tope_obligacion_patrimonio_uvt: int
    tope_obligacion_consignaciones_uvt: int
    limite_general_pct: float
    limite_general_uvt: int
    exenta_laboral_pct: float
    exenta_laboral_tope_uvt: int
    dependiente_uvt: int
    dependientes_max: int
    ded_387_pct: float
    ded_387_tope_uvt_mes: int
    prepagada_tope_uvt_anio: int
    intereses_vivienda_tope_uvt: int
    icetex_tope_uvt: int
    afc_pct: float
    afc_tope_uvt: int
    gmf_pct_deducible: float
    facturas_pct: float
    facturas_tope_uvt: int
    pension_exenta_uvt_mes: int
    dividendos_tarifa_gravados: float
    descuento_dividendos_pct: float
    descuento_dividendos_umbral_uvt: int
    donaciones_descuento_pct: float
    componente_inflacionario: float | None
    anticipo_pct: list[float]
    tabla_241: list[Tramo]

    def uvt_pesos(self, n: float) -> int:
        return pesos(Decimal(str(n)) * self.uvt)

    @model_validator(mode="after")
    def _validar_uvt_contra_la_tabla(self) -> Self:
        """El YAML repite la UVT del año; acá se ata a la tabla única de `parametros`.

        El motor lee `uvt` y `uvt_siguiente` de acá (es lo que hace `uvt_pesos`), así que
        el valor tiene que estar en el YAML. Pero un valor repetido es un valor que puede
        quedar viejo: si el decreto sube la UVT y solo se actualiza la tabla, este YAML
        seguiría liquidando con la del año pasado y ningún test lo notaría.

        Un año que la tabla todavía no conoce se deja pasar: el YAML de un año gravable
        nuevo tiene que poder cargarse sin que esta guarda lo bloquee.
        """
        # Import diferido: `parametros/__init__` importa este módulo, así que a nivel de
        # módulo sería circular. Al validar, el paquete ya está cargado.
        from declaras.parametros import UVT_POR_ANIO

        esperada = UVT_POR_ANIO.get(self.anio)
        if esperada is not None and self.uvt != esperada:
            raise ValueError(
                f"uvt: el YAML del año {self.anio} declara {self.uvt}, pero la tabla de "
                f"parametros dice {esperada}"
            )
        siguiente = UVT_POR_ANIO.get(self.anio + 1)
        if siguiente is not None and self.uvt_siguiente != siguiente:
            raise ValueError(
                f"uvt_siguiente: el YAML del año {self.anio} declara {self.uvt_siguiente} "
                f"para {self.anio + 1}, pero la tabla de parametros dice {siguiente}"
            )
        return self

    @model_validator(mode="after")
    def _validar_tabla_241(self) -> Self:
        """La tabla debe cubrir [0, ∞) en tramos ascendentes y contiguos.

        `impuesto_tabla_241` la recorre asumiendo eso: sin la guarda, un YAML
        desordenado o con huecos daría una cifra mala en silencio.
        """
        tramos = self.tabla_241
        if not tramos:
            raise ValueError("tabla_241: no puede estar vacía")
        if tramos[0].desde_uvt != 0:
            raise ValueError(
                "tabla_241: el primer tramo debe empezar en desde_uvt=0, "
                f"no en {tramos[0].desde_uvt}"
            )
        ultimo = len(tramos) - 1
        if tramos[ultimo].hasta_uvt is not None:
            raise ValueError(
                "tabla_241: el último tramo debe ser abierto (hasta_uvt nulo); tiene "
                f"hasta_uvt={tramos[ultimo].hasta_uvt}, así que las bases por encima "
                "quedarían sin gravar"
            )
        for i, tramo in enumerate(tramos):
            if tramo.hasta_uvt is None:
                if i != ultimo:
                    raise ValueError(
                        "tabla_241: solo el último tramo puede tener hasta_uvt nulo; "
                        f"lo tiene el tramo {i} (desde_uvt={tramo.desde_uvt})"
                    )
                continue
            if tramo.hasta_uvt <= tramo.desde_uvt:
                raise ValueError(
                    f"tabla_241: los tramos deben ser ascendentes; el tramo {i} va de "
                    f"desde_uvt={tramo.desde_uvt} a hasta_uvt={tramo.hasta_uvt}"
                )
            if i != ultimo and tramo.hasta_uvt != tramos[i + 1].desde_uvt:
                raise ValueError(
                    f"tabla_241: los tramos deben ser contiguos; el tramo {i} termina en "
                    f"hasta_uvt={tramo.hasta_uvt} pero el siguiente empieza en "
                    f"desde_uvt={tramos[i + 1].desde_uvt}"
                )
        return self
