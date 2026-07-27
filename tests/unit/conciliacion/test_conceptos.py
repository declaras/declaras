from declaras.services.conciliacion import Concepto, concepto_de_codigo


def test_codigos_conocidos():
    assert concepto_de_codigo("5001") is Concepto.SALARIOS
    assert concepto_de_codigo("5004") is Concepto.SERVICIOS


def test_codigo_desconocido_devuelve_none_no_un_default():
    assert concepto_de_codigo("9999") is None
    assert concepto_de_codigo("") is None


def test_dos_codigos_distintos_pueden_normalizar_al_mismo_concepto():
    """5002 (honorarios) y 5003 (comisiones) son el mismo hecho para el cruce."""
    assert concepto_de_codigo("5002") is Concepto.HONORARIOS
    assert concepto_de_codigo("5003") is Concepto.HONORARIOS
