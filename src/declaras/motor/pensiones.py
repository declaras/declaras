from declaras.caso import CasoTributario
from declaras.motor.traza import Traza
from declaras.parametros import ParametrosAnio


def rlg_pensiones(caso: CasoTributario, p: ParametrosAnio, t: Traza) -> int:
    """Cédula de pensiones: exención de 1.000 UVT POR MES; el exceso grava."""
    tope_mes = p.uvt_pesos(p.pension_exenta_uvt_mes)
    gravado = sum(
        max(0, mesada - tope_mes)
        for pension in caso.pensiones
        for mesada in pension.mesadas
    )
    return t.nodo(
        "RLG_PENSIONES", "Renta líquida gravable cédula de pensiones",
        gravado,
        f"Σ max(0, mesada_mes − 1.000 UVT = {tope_mes:,}) sobre 12 meses",
        regla="art. 206 num. 5 ET",
    )
