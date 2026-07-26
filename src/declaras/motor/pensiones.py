from declaras.caso import CasoTributario
from declaras.motor.traza import Traza
from declaras.parametros import ParametrosAnio


def rlg_pensiones(caso: CasoTributario, p: ParametrosAnio, t: Traza) -> int:
    """Cédula de pensiones: exención de 1.000 UVT POR MES; el exceso grava.

    La exención es del contribuyente, no de cada pagador: con varias pensiones
    concurrentes (p. ej. Colpensiones + fondo privado, o pensión propia +
    sustitución) se agregan las mesadas del mismo mes y el tope se resta UNA vez.
    """
    tope_mes = p.uvt_pesos(p.pension_exenta_uvt_mes)
    # El schema garantiza 12 mesadas por pensión (enero a diciembre), así que los
    # índices de mes están alineados entre pagadores.
    agregado_mes = [
        sum(pension.mesadas[mes] for pension in caso.pensiones)
        for mes in range(12)
    ]
    gravado = sum(max(0, total - tope_mes) for total in agregado_mes)
    return t.nodo(
        "RLG_PENSIONES", "Renta líquida gravable cédula de pensiones",
        gravado,
        f"Σ max(0, mesada_mes agregada entre pagadores − "
        f"{p.pension_exenta_uvt_mes:,} UVT = {tope_mes:,}) sobre 12 meses",
        regla="art. 206 num. 5 ET",
    )
