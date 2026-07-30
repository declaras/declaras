"""El formato 2276 de la exógena: siete conceptos distintos bajo un solo código.

EL BUG MÁS CARO QUE SE HA ENCONTRADO EN ESTE PROYECTO, y este archivo es lo que impide que vuelva.

El 2276 NO es un concepto: es el formato de reporte de rentas de trabajo y pensiones. Estaba mapeado
entero a `Concepto.SALARIOS`, así que el cruce sumaba como sueldo las siete filas que trae dentro,
incluidas tres que no son ingreso. Medido contra el reporte real de un solo empleador:

    sueldo declarado      $63.925.000     ← todas las filas sumadas
    sueldo real           $56.485.000
    aportes obligatorios   $3.940.000     ← SUMABAN al ingreso en vez de restar como INCRNGO
    base inflada          $11.380.000     ← lo que suma de más, más lo que deja de restar

Y la casilla 33 del formulario quedaba en cero, que es justo la que la DIAN compara contra lo que
ella misma precargó: la diferencia más visible que un cruce puede encontrar.

LA FUENTE QUE LO RESUELVE YA ESTABA EN EL SISTEMA. Cada fila de la exógena trae la columna "Uso
declaración Sugerida", donde la propia DIAN dice a qué renglón del 210 va el valor. Los aportes van
a R33 (ingresos no constitutivos de renta), no a R32. El lector ya dejaba eso en `form_lines` y el
clasificador solo miraba el código.

Y DE PASO DESBLOQUEÓ LA EXENCIÓN DE CESANTÍAS: el promedio salarial del semestre, que es el dato del
que depende (art. 206 num. 4), es una de esas siete filas. Se creía que había que pedírselo al
empleador; venía en el reporte, sumándose al sueldo como si fuera un pago.
"""

from __future__ import annotations

import pytest

from declaras.caso import Contribuyente
from declaras.motor import liquidar
from declaras.motor.elecciones import Elecciones
from declaras.parametros import cargar
from declaras.services.conciliacion import Concepto, a_caso, abrir, autorresolver
from declaras.services.conciliacion.conceptos import concepto_de_codigo
from declaras.services.conciliacion.cruce import _concepto_de_fila
from tests.unit.conciliacion.test_cruce import _exogena

P = cargar(2025)
NIT = "900555111"

# ── LAS SIETE FILAS, transcritas del reporte real de un contribuyente ─────────────────────────
#
# (texto oficial de la DIAN, renglones que ella asigna, monto, concepto que le corresponde)
#
# Los renglones son literales del reporte: los aportes llegan marcados con varios a la vez porque el
# mismo aporte se imputa a la cédula donde esté el ingreso que lo generó.
FILAS_DEL_2276: tuple[tuple[str, list[int], int, Concepto], ...] = (
    ("Pagos por salarios (Concepto: 2276)", [32], 49_250_000, Concepto.SALARIOS),
    ("Pagos por prestaciones sociales (Concepto: 2276)", [32], 4_771_000, Concepto.SALARIOS),
    (
        "Cesantías e intereses de cesantías pagadas al empleado (Concepto: 2276)",
        [32],
        179_000,
        Concepto.CESANTIAS,
    ),
    (
        "Cesantías consignadas al fondo de cesantías (Concepto: 2276)",
        [29, 36],
        2_285_000,
        Concepto.CESANTIAS,
    ),
    (
        "Aporte obligatorio fondos pensiones y solidaridad a cargo del trabajador (Concepto: 2276)",
        [33, 59, 76],
        1_970_000,
        Concepto.APORTES_PENSION,
    ),
    (
        "Aportes obligatorios a salud a cargo Trabajador (Concepto: 2276)",
        [33, 59, 100],
        1_970_000,
        Concepto.APORTES_SALUD,
    ),
    (
        "Valor ingreso laboral promedio de los últimos seis meses (Concepto: 2276)",
        [36],
        3_500_000,
        Concepto.PROMEDIO_SALARIAL,
    ),
)

# Lo que de verdad es ingreso laboral: salarios, prestaciones y las dos filas de cesantías.
INGRESO_REAL = 49_250_000 + 4_771_000 + 179_000 + 2_285_000
APORTES = 1_970_000 + 1_970_000
CESANTIAS = 179_000 + 2_285_000
PROMEDIO_MES = 3_500_000
# Lo que sumaba el mapeo viejo: las siete filas.
SUMA_DE_TODO = sum(f[2] for f in FILAS_DEL_2276)


def _fila_2276(texto: str, renglones: list[int], monto: int, nit: str = NIT) -> dict:
    """Una fila del 2276 como la emite el lector de exógena."""
    return {
        "reporter_nit": nit,
        "reporter_name": "INVERSIONES DEMO SAS",
        "reported_id_number": "1234567",
        "reported_name": "PRUEBA",
        "concept": texto,
        "concept_code": "2276",
        "amount": monto,
        "form_lines": renglones,
        "suggested_use": " | ".join(f"R{r}" for r in renglones),
    }


def _todas_las_filas() -> list[dict]:
    return [_fila_2276(t, r, m) for t, r, m, _ in FILAS_DEL_2276]


def _caso_del_2276():
    partidas = autorresolver(abrir(_exogena(*_todas_las_filas())))
    caso = a_caso(
        partidas, contribuyente=Contribuyente(num_doc="1234567", nombre="X"), anio_gravable=2025
    )
    return partidas, caso


# ── el código no clasifica, porque es un formato ──────────────────────────────────────────────


def test_el_2276_no_tiene_concepto_por_codigo() -> None:
    """Es la raíz del bug: un código de FORMATO mapeado a un concepto de ingreso."""
    assert concepto_de_codigo("2276") is None


@pytest.mark.parametrize(
    ("texto", "renglones", "monto", "esperado"),
    FILAS_DEL_2276,
    ids=[f[0][:34] for f in FILAS_DEL_2276],
)
def test_cada_fila_del_2276_se_clasifica_por_lo_que_es(
    texto: str, renglones: list[int], monto: int, esperado: Concepto
) -> None:
    """Fila por fila contra el reporte real. Tres de las siete NO son ingreso."""
    concepto, nota = _concepto_de_fila(_fila_2276(texto, renglones, monto), "2276")

    assert concepto is esperado, f"{texto[:50]} debería ser {esperado}"
    assert nota is None


def test_las_siete_filas_abren_cinco_partidas_distintas() -> None:
    """Salarios y prestaciones comparten partida (los dos son nómina) y las cesantías también.

    Antes abrían UNA sola, con la suma de todo dentro.
    """
    partidas = abrir(_exogena(*_todas_las_filas()))

    por_concepto = {str(p.concepto): p for p in partidas}
    assert set(por_concepto) == {
        "SALARIOS",
        "CESANTIAS",
        "APORTES_SALUD",
        "APORTES_PENSION",
        "PROMEDIO_SALARIAL",
    }
    assert por_concepto["SALARIOS"].version_dian.monto == 49_250_000 + 4_771_000
    assert por_concepto["CESANTIAS"].version_dian.monto == CESANTIAS


# ── la cifra: lo que se declaraba de más ──────────────────────────────────────────────────────


def test_el_ingreso_laboral_es_el_real_y_no_la_suma_de_las_siete_filas() -> None:
    """LA MEDIDA DEL BUG. $56.485.000 de sueldo, no $63.925.000."""
    _, caso = _caso_del_2276()

    [laboral] = caso.laborales
    assert laboral.bruto == INGRESO_REAL
    assert laboral.bruto == 56_485_000
    assert SUMA_DE_TODO == 63_925_000, "la suma de las siete filas, que es lo que declaraba antes"
    assert laboral.bruto < SUMA_DE_TODO
    assert SUMA_DE_TODO - laboral.bruto == 7_440_000, "lo que se sumaba de más"


def test_los_aportes_obligatorios_restan_en_vez_de_sumar() -> None:
    """Son INCRNGO (art. 55 y 56): la DIAN los manda a R33, no a R32.

    Sumarlos al ingreso equivoca la base DOS VECES: por lo que suma y por lo que deja de restar.
    """
    _, caso = _caso_del_2276()

    [laboral] = caso.laborales
    assert laboral.aportes_salud == 1_970_000
    assert laboral.aportes_pension == 1_970_000
    assert laboral.aportes_salud + laboral.aportes_pension == APORTES

    t = liquidar(caso, P, Elecciones())
    assert t.valor("INCR_APORTES") == APORTES
    assert t.valor("ING_NETOS_GENERAL") == INGRESO_REAL - APORTES


def test_el_promedio_salarial_no_es_plata_que_se_declare() -> None:
    """ "Valor ingreso laboral promedio de los últimos seis meses" es un DATO, no un pago.

    Es la fila más absurda de las tres: $3.500.000 de ingreso completamente inventado.
    """
    _, caso = _caso_del_2276()

    [laboral] = caso.laborales
    assert laboral.promedio_mensual_6m == PROMEDIO_MES
    assert PROMEDIO_MES not in (laboral.salarios, laboral.bruto)
    # Si se hubiera colado al sueldo, el bruto sería mayor.
    assert laboral.bruto == INGRESO_REAL


# ── lo que el arreglo desbloqueó: la exención de cesantías, sola ──────────────────────────────


def test_las_cesantias_quedan_en_su_campo_y_no_dentro_del_sueldo() -> None:
    """El motor necesita saber CUÁNTO de la nómina son cesantías para aplicarles su exención.

    Siguen contando en el bruto (son ingreso del año, art. 27 num. 3) pero aparte.
    """
    _, caso = _caso_del_2276()

    [laboral] = caso.laborales
    assert laboral.cesantias_e_intereses == CESANTIAS
    assert laboral.salarios == 49_250_000 + 4_771_000
    assert laboral.bruto == laboral.salarios + laboral.cesantias_e_intereses


def test_la_exencion_de_cesantias_se_aplica_sin_pedirle_nada_al_cliente() -> None:
    """EL PROMEDIO VIENE EN LA EXÓGENA, y eso es lo que hace que esto funcione solo.

    $3.500.000 al mes son 70 UVT, muy por debajo de las 350 UVT del art. 206 num. 4, así que las
    cesantías quedan 100% exentas. La primera versión de la exención asumía que este dato había que
    pedírselo al empleador.
    """
    _, caso = _caso_del_2276()
    t = liquidar(caso, P, Elecciones())

    assert PROMEDIO_MES / P.uvt < 350, "el promedio está bajo el tope de la exención total"
    assert t.valor("EXENTA_CESANTIAS") == CESANTIAS
    assert not [f for f in t.flags if f.codigo == "CESANTIAS_SIN_PROMEDIO_SALARIAL"]


def test_sin_la_fila_del_promedio_las_cesantias_se_gravan_y_se_avisa() -> None:
    """Un empleador que no reporta el promedio deja la exención sin sostén.

    Ahí sí hay que pedirle la certificación, y el aviso lo dice con la plata en juego.
    """
    filas = [f for f in _todas_las_filas() if "promedio" not in f["concept"]]
    partidas = autorresolver(abrir(_exogena(*filas)))
    caso = a_caso(
        partidas, contribuyente=Contribuyente(num_doc="1234567", nombre="X"), anio_gravable=2025
    )

    [laboral] = caso.laborales
    assert laboral.promedio_mensual_6m is None
    assert laboral.cesantias_e_intereses == CESANTIAS

    t = liquidar(caso, P, Elecciones())
    assert t.valor("EXENTA_CESANTIAS") == 0
    assert [f for f in t.flags if f.codigo == "CESANTIAS_SIN_PROMEDIO_SALARIAL"]


def test_con_varias_filas_de_promedio_se_toma_la_mayor() -> None:
    """La mayor es la que MENOS exención concede: cae en un tramo de menor porcentaje.

    Escoger la que más conviene sobre un hecho repetido y sin conciliar sería bajar el impuesto por
    la vía de elegir la fuente.
    """
    filas = _todas_las_filas()
    filas.append(
        _fila_2276(
            "Valor ingreso laboral promedio de los últimos seis meses (2276)", [36], 400 * P.uvt
        )
    )
    partidas = autorresolver(abrir(_exogena(*filas)))
    caso = a_caso(
        partidas, contribuyente=Contribuyente(num_doc="1234567", nombre="X"), anio_gravable=2025
    )

    [laboral] = caso.laborales
    assert laboral.promedio_mensual_6m == 400 * P.uvt
    # 400 UVT cae en el tramo del 90%, no en la exención total.
    t = liquidar(caso, P, Elecciones())
    assert t.valor("EXENTA_CESANTIAS") == round(CESANTIAS * 0.90)


# ── las dos guardas del clasificador, que salieron de bugs propios ────────────────────────────


@pytest.mark.parametrize(
    ("codigo", "texto"),
    [
        ("2214", "Activos aportes parafiscales, salud, pensión y cesantías (Concepto: 2214)"),
        ("2215", "Activos laborales reales consolidados trabajador sin cesantías (Concepto: 2215)"),
    ],
)
def test_el_texto_no_pisa_un_codigo_que_ya_esta_bien_mapeado(codigo: str, texto: str) -> None:
    """BUG PROPIO, MEDIDO. Con el clasificador por texto puesto ANTES del código, estas dos filas
    de PATRIMONIO caían como aportes del año por decir "salud" y "cesantías" en su nombre.

    Son saldos al 31 de diciembre. Convertirlas en aportes habría inventado una deducción de
    $6.430.250 en el caso real, que es la dirección peligrosa: baja el impuesto sin derecho.
    """
    fila = _fila_2276(texto, [29], 4_250_000)
    fila["concept_code"] = codigo

    concepto, _ = _concepto_de_fila(fila, codigo)

    assert concepto is Concepto.PATRIMONIO


def test_r32_solo_es_nomina_y_r32_acompanado_no_se_asume() -> None:
    """R32 es "Ingresos brutos (rentas de trabajo)" y ahí la DIAN manda la nómina.

    Se exige que sea el ÚNICO renglón: una fila que toca R32 y algo más es otra cosa (las cesantías
    consignadas van a R29 y R36 a la vez) y no se clasifica a la ligera.
    """
    solo = _fila_2276("Un pago cualquiera de nómina", [32], 1_000_000)
    solo["concept_code"] = "9999"
    acompanado = _fila_2276("Un pago con dos destinos", [32, 96], 1_000_000)
    acompanado["concept_code"] = "9999"

    assert _concepto_de_fila(solo, "9999")[0] is Concepto.SALARIOS
    assert _concepto_de_fila(acompanado, "9999")[0] is None


def test_los_honorarios_no_caen_en_r32_asi_que_no_reciben_trato_de_nomina() -> None:
    """VERIFICADO CONTRA EL REPORTE REAL: la DIAN manda servicios a R43 y otros ingresos a R74.

    Importa porque las rentas de trabajo del art. 103 incluyen honorarios, así que si la DIAN los
    mandara a R32 la regla de arriba les daría el 25% exento sin verificar la condición del art. 206
    par. 5. No lo hace: R32 queda para nómina.
    """
    servicios = _fila_2276("Servicios (Concepto: 5004)", [43], 4_240_000)
    servicios["concept_code"] = "5004"
    otros = _fila_2276("Otros ingresos (Concepto: 5016)", [74], 1_383_876)
    otros["concept_code"] = "5016"

    assert _concepto_de_fila(servicios, "5004")[0] is Concepto.SERVICIOS
    assert _concepto_de_fila(otros, "5016")[0] is Concepto.OTROS


def test_una_fila_de_incrngo_que_el_texto_no_nombra_igual_no_entra_como_ingreso() -> None:
    """El renglón alcanza para saber que NO es ingreso, aunque no diga cuál aporte es.

    Es el respaldo de la regla: un aporte con una redacción que la tabla de textos no cubre sigue
    restando en vez de sumar.
    """
    fila = _fila_2276("Un aporte con nombre que nadie previó", [33], 500_000)
    fila["concept_code"] = "9999"

    concepto, _ = _concepto_de_fila(fila, "9999")

    assert concepto is Concepto.APORTES_SALUD
    assert concepto not in (Concepto.SALARIOS, Concepto.OTROS)
