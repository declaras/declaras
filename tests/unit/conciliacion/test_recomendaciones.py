"""Cuánta plata hay en juego, beneficio por beneficio, esté o no pedido.

LO QUE ESTO PROTEGE es la diferencia entre tres conclusiones que llevan a acciones opuestas y que
antes se veían iguales en pantalla, todas como "$ 0":

    hay plata en juego     → vale la pena buscar los certificados
    ninguno baja nada      → NO vale la pena: ya no queda impuesto que bajar
    no se pudo calcular    → no se sabe; primero hay que resolver lo que bloquea el cálculo

La segunda y la tercera son las peligrosas: si se confunden, o se manda a alguien a buscar papeles
que no le sirven, o se le dice "no te ahorras nada" cuando la verdad es que nadie lo midió.
"""

from datetime import UTC, datetime

from declaras.caso import (
    Beneficios,
    CasoTributario,
    Contribuyente,
    Fuente,
    MontoDeclarado,
)
from declaras.parametros import cargar
from declaras.services.conciliacion import (
    Concepto,
    Decision,
    Motivo,
    Respuesta,
    a_caso,
    abrir,
    autorresolver,
    resolver,
)
from declaras.services.conciliacion.recomendaciones import (
    EstadoBeneficio,
    derivar_recomendaciones,
)
from tests.unit.conciliacion.test_cruce import _exogena, _fila

AHORA = datetime(2026, 7, 27, tzinfo=UTC)
CONTRIB = Contribuyente(num_doc="1234567", nombre="Prueba")
P = cargar(2025)

# Un salario que sí paga impuesto: 87 millones está muy por encima del tramo del 0% (1.090 UVT),
# así que acá una deducción de verdad baja plata. Con un sueldo bajo todos los ahorros son cero y
# el test no probaría nada.
SUELDO_QUE_PAGA = 87_400_000


def _caso_con_sueldo(monto: int = SUELDO_QUE_PAGA) -> tuple[list, CasoTributario]:
    partidas = autorresolver(abrir(_exogena(_fila("900111222", "5001", monto))))
    return list(partidas), a_caso(partidas, contribuyente=CONTRIB, anio_gravable=2025)


def test_el_catalogo_sale_completo_aunque_no_haya_nada_pendiente() -> None:
    """El bug que motivó el módulo: contestar "no tengo" borraba la cifra de lo que se perdía.

    `derivar_peticiones` descarta lo contestado, que es correcto para una cola de trabajo. Acá no,
    porque la pregunta es "cuánta plata hay en juego" y un beneficio descartado sigue teniendo una
    respuesta a eso.
    """
    partidas, caso = _caso_con_sueldo()
    todas = [
        Respuesta(pregunta=p, tiene=False, detalle={}, quien="cliente", cuando=AHORA)
        for p in ("PREPAGADA", "DEPENDIENTES", "INTERESES_VIVIENDA", "ICETEX", "AFC_FVP")
    ]

    recos = derivar_recomendaciones(partidas, todas, caso, p=P)

    assert len(recos.items) >= 5, "el catálogo tiene que salir completo, no filtrado"
    descartados = [r for r in recos.items if r.estado is EstadoBeneficio.DESCARTADO]
    assert len(descartados) == 5
    assert all(r.ahorro_por_que or r.ahorro > 0 for r in descartados), (
        "un descartado sin cifra y sin razón no dice nada"
    )


def test_lo_que_el_cliente_dijo_que_no_tiene_conserva_lo_que_habria_ahorrado() -> None:
    """Un "no" dado por error cuesta plata, y para verlo hay que saber cuánta."""
    partidas, caso = _caso_con_sueldo()
    dijo_que_no = [
        Respuesta(pregunta="DEPENDIENTES", tiene=False, detalle={}, quien="cliente", cuando=AHORA)
    ]

    recos = derivar_recomendaciones(partidas, dijo_que_no, caso, p=P)
    dependientes = next(r for r in recos.items if r.pregunta == "DEPENDIENTES")

    assert dependientes.estado is EstadoBeneficio.DESCARTADO
    assert dependientes.ahorro > 0, (
        "descartar el beneficio no puede borrar la cifra de lo que habría ahorrado: es "
        "justamente lo que hace falta para saber si vale la pena volver a preguntar"
    )
    assert dependientes.medido


def test_hay_plata_en_juego_cuando_el_sueldo_paga_impuesto() -> None:
    partidas, caso = _caso_con_sueldo()

    recos = derivar_recomendaciones(partidas, [], caso, p=P)

    assert not recos.ninguno_ahorra
    assert recos.ahorro_disponible > 0
    mueven = [r for r in recos.items if r.vale_la_pena]
    assert mueven, "con un sueldo de 87 millones alguna deducción tiene que bajar impuesto"
    assert recos.ahorro_disponible == sum(r.ahorro for r in mueven)


def test_ninguno_ahorra_cuando_el_impuesto_ya_es_cero() -> None:
    """La conclusión más útil: no vale la pena salir a buscar certificados.

    Con un sueldo por debajo del primer tramo del artículo 241 el impuesto es cero, así que ninguna
    deducción lo baja. Decirle a alguien "podrías ahorrar $3.098.900 con prepagada" ahí sería falso.
    """
    partidas, caso = _caso_con_sueldo(30_000_000)

    recos = derivar_recomendaciones(partidas, [], caso, p=P)

    assert recos.ninguno_ahorra
    assert recos.ahorro_disponible == 0
    medidos = [r for r in recos.items if r.medido]
    assert medidos, "la conclusión exige mediciones: sin ellas no se puede afirmar nada"
    assert all(r.ahorro == 0 for r in medidos)
    assert any("no queda impuesto" in (r.ahorro_por_que or "") for r in medidos), (
        "hay que decir POR QUÉ no ahorra, o parece una falla del cálculo"
    )


def test_no_se_afirma_que_ninguno_ahorra_cuando_ninguno_se_pudo_medir() -> None:
    """El bug que encontré con un caso real: siete beneficios sin medir daban `ninguno_ahorra`.

    Un ingreso llevado a mano deja un aviso BLOQUEANTE, el optimizador se niega a correr sobre una
    base incompleta y ningún ahorro se puede estimar. La pantalla afirmaba "no te ahorras nada"
    sobre cero evidencia, que es la conclusión opuesta a la verdadera: falta resolver algo antes.
    """
    # 5004 es SERVICIOS, que está en CONCEPTOS_FUERA_DEL_MOTOR: es el único caso en que
    # LLEVAR_A_MANO aplica, porque existe para conceptos que el motor todavía no liquida.
    partidas = abrir(_exogena(_fila("900111222", "5004", 40_000_000)))
    a_mano = [
        resolver(p, Decision.LLEVAR_A_MANO, motivo=Motivo.FUERA_DEL_MOTOR, quien="contador@x.co")
        for p in partidas
        if p.concepto is Concepto.SERVICIOS
    ]
    assert a_mano, "el fixture tiene que producir la partida que se lleva a mano"
    caso = CasoTributario(anio_gravable=2025, contribuyente=CONTRIB)

    recos = derivar_recomendaciones(a_mano, [], caso, p=P)

    assert recos.sin_medir == len(recos.items), "ninguno se pudo medir en este estado"
    assert not recos.ninguno_ahorra, (
        "no se puede concluir que ningún beneficio ahorra cuando ninguno se pudo calcular: "
        "son conclusiones opuestas y llevan a acciones opuestas"
    )


def test_un_beneficio_ya_capturado_sale_como_aplicado_y_no_como_recomendacion() -> None:
    """Lo que ya está dentro de la cifra no es una recomendación: es un hecho."""
    partidas, caso = _caso_con_sueldo()
    con_prepagada = caso.model_copy(
        update={
            "beneficios": Beneficios(
                medicina_prepagada=MontoDeclarado(
                    valor=4_000_000, fuente=Fuente.fixture("certificado de prueba")
                )
            )
        }
    )

    recos = derivar_recomendaciones(partidas, [], con_prepagada, p=P)
    prepagada = next(r for r in recos.items if r.pregunta == "PREPAGADA")

    assert prepagada.estado is EstadoBeneficio.APLICADO
    assert not prepagada.vale_la_pena, (
        "un beneficio aplicado no puede sumar al ahorro disponible: ya está descontado en la "
        "cifra que la persona ve como su impuesto, y contarlo otra vez la duplicaría"
    )


def test_el_techo_se_marca_para_no_prometer_una_cifra_que_nadie_sostiene() -> None:
    """Cuánto pagó de prepagada lo sabe el cliente. La estimación es sobre el tope legal."""
    partidas, caso = _caso_con_sueldo()

    recos = derivar_recomendaciones(partidas, [], caso, p=P)
    prepagada = next(r for r in recos.items if r.pregunta == "PREPAGADA")

    assert prepagada.ahorro_es_techo, (
        "sin esta marca la pantalla escribe la cifra a secas y el contador le promete al cliente "
        "un ahorro que depende de cuánto haya pagado"
    )
    assert prepagada.tope and prepagada.tope > 0


def test_la_cifra_es_impuesto_y_no_reduccion_de_la_base() -> None:
    """Son dos números muy distintos y el que importa es el primero.

    El tope de la deducción de dependientes son 72 UVT de BASE; lo que baja el impuesto es una
    fracción de eso, la que corresponde a la tarifa marginal. Si el ahorro reportado fuera igual o
    mayor que el tope de base, la cifra sería la equivocada.
    """
    partidas, caso = _caso_con_sueldo()

    recos = derivar_recomendaciones(partidas, [], caso, p=P)
    dependientes = next(r for r in recos.items if r.pregunta == "DEPENDIENTES")

    assert dependientes.ahorro > 0
    assert dependientes.tope is not None
    assert dependientes.ahorro < dependientes.tope, (
        f"el ahorro reportado ({dependientes.ahorro}) no puede ser mayor o igual al tope de base "
        f"({dependientes.tope}): eso significaría que se está mostrando reducción de base como si "
        "fuera impuesto ahorrado"
    )


def test_el_orden_no_baila_entre_consultas() -> None:
    """Con varios ahorros iguales (típicamente cero) la lista tiene que salir estable."""
    partidas, caso = _caso_con_sueldo(30_000_000)

    primera = derivar_recomendaciones(partidas, [], caso, p=P)
    segunda = derivar_recomendaciones(partidas, [], caso, p=P)

    assert [r.pregunta for r in primera.items] == [r.pregunta for r in segunda.items]


def test_ninguna_etiqueta_le_llega_al_titular_como_clave_de_tabla() -> None:
    """ "AFC_FVP" y "DONACION_ESAL" son claves del catálogo, no nombres de nada."""
    partidas, caso = _caso_con_sueldo()

    recos = derivar_recomendaciones(partidas, [], caso, p=P)

    for r in recos.items:
        assert r.etiqueta, f"{r.pregunta} salió sin etiqueta"
        assert r.etiqueta != r.pregunta, f"{r.pregunta} le llega al titular como clave de tabla"
        assert "_" not in r.etiqueta, f"{r.etiqueta!r} trae un identificador del código"
