"""El borrador de la DIAN contra el nuestro, casilla por casilla.

La DIAN precrea un borrador del 210 que se puede firmar en tres clics. Se ve oficial pero es una
sugerencia. Esta comparación es lo que le da sentido a haberlo descargado: sin ella, la única forma
de saber si el trabajo con documentos sirvió era confiar en el número final.

MEDIDO CONTRA EL CASO REAL: la comparación cruda daba 77 casillas "distintas" de 80, casi todas
porque un lado no trae la casilla y el otro la trae en cero. Filtrando por plata quedan 29, y entre
ellas aparecieron dos que le cuestan al cliente: $3.940.000 de aportes que la DIAN tiene y nosotros
no, y $36.000 del 1% de compras con factura electrónica.
"""

from __future__ import annotations

from declaras.documents.models import DocumentReading, ExtractedField
from declaras.render import Casilla
from declaras.services.comparacion_210 import Diferencia, comparar


def _reading(**casillas: int) -> DocumentReading:
    """Una lectura del 210 como la produce el parser: campos `casilla_N`."""
    return DocumentReading(
        doc_type="SUGGESTED_RETURN",
        parser="renta210.pdf.v2",
        content_sha256="0" * 64,
        fields=[
            ExtractedField(name=f"casilla_{n.removeprefix('c')}", value=v, unit="COP")
            for n, v in casillas.items()
        ],
    )


def _nuestras(**casillas: int) -> list[Casilla]:
    return [
        Casilla(numero=int(n.removeprefix("c")), nombre=f"Casilla {n}", valor=v)
        for n, v in casillas.items()
    ]


# ── las cuatro relaciones ─────────────────────────────────────────────────────────────────────────


def test_una_casilla_igual_no_sale_como_diferencia() -> None:
    c = comparar(_nuestras(c32=10_000_000), _reading(c32=10_000_000))

    assert c.disponible
    assert c.coinciden
    assert c.casillas[0].diferencia is Diferencia.IGUALES
    assert not c.con_diferencia


def test_declarar_mas_que_la_dian_se_marca_como_tal() -> None:
    """Lo normal cuando el trabajo con documentos aporta: aparece algo que la DIAN no vio."""
    c = comparar(_nuestras(c32=15_000_000), _reading(c32=10_000_000))

    [casilla] = c.con_diferencia
    assert casilla.diferencia is Diferencia.MAYOR_NUESTRA
    assert casilla.delta == 5_000_000


def test_declarar_menos_que_la_dian_queda_aparte_porque_es_la_mitad_peligrosa() -> None:
    """La DIAN tiene un dato que no estamos declarando, y eso lo cruza sola.

    En el caso real fue la casilla 33: $3.940.000 de aportes de salud y pensión que la DIAN tenía y
    el cálculo no, o sea una deducción perdida.
    """
    c = comparar(_nuestras(c33=0), _reading(c33=3_940_000))

    [casilla] = c.menores_que_la_dian
    assert casilla.numero == 33
    assert casilla.diferencia is Diferencia.MENOR_NUESTRA
    assert casilla.delta == -3_940_000


def test_una_casilla_que_solo_trae_la_dian_se_compara_igual() -> None:
    """Filtrar por las nuestras esconderían justo las que hay que mirar.

    En el caso real fue el 1% de compras con factura electrónica: la DIAN lo precargó con $36.000 y
    nuestro formulario no traía la casilla, así que ese beneficio se estaba perdiendo.
    """
    c = comparar(_nuestras(c32=1), _reading(c32=1, c28=36_000))

    [casilla] = c.con_diferencia
    assert casilla.numero == 28
    assert casilla.diferencia is Diferencia.SOLO_DE_LA_DIAN
    assert casilla.nuestra is None
    assert casilla.delta == -36_000


def test_ausente_no_es_lo_mismo_que_cero() -> None:
    """`None` dice "este lado no trae la casilla"; 0 dice "la trae y vale cero".

    Colapsarlos perdería la diferencia entre un formulario que no llegó a esa casilla y uno que la
    calculó en cero, que llevan a revisiones distintas.
    """
    c = comparar(_nuestras(c32=0), _reading(c33=0))

    por_numero = {x.numero: x for x in c.casillas}
    assert por_numero[32].nuestra == 0 and por_numero[32].de_la_dian is None
    assert por_numero[33].nuestra is None and por_numero[33].de_la_dian == 0


# ── el filtro que hace la lista legible ───────────────────────────────────────────────────────────


def test_una_casilla_ausente_contra_cero_no_ensucia_la_lista() -> None:
    """El ruido que hacía inservible la comparación: 73 de 77 filas no movían un peso."""
    c = comparar(_nuestras(c32=1_000), _reading(c32=1_000, c38=0, c39=0, c40=0))

    assert not c.con_diferencia, "una casilla ausente frente a cero no cambia ninguna cifra"
    assert c.coinciden
    # La relación estructural se conserva para quien la necesite.
    assert {x.diferencia for x in c.casillas if x.numero in (38, 39, 40)} == {
        Diferencia.SOLO_DE_LA_DIAN
    }


def test_las_diferencias_salen_ordenadas_por_plata() -> None:
    """Lo que más mueve, arriba: es el orden en que alguien revisa."""
    c = comparar(
        _nuestras(c32=10_000_000, c33=0, c29=5_000),
        _reading(c32=1_000_000, c33=3_940_000, c29=1_000),
    )

    assert [x.numero for x in c.con_diferencia] == [32, 33, 29]
    assert [abs(x.delta) for x in c.con_diferencia] == [9_000_000, 3_940_000, 4_000]


def test_el_numero_de_casilla_desempata_para_que_la_lista_no_baile() -> None:
    c = comparar(_nuestras(c58=1_000, c32=1_000), _reading(c58=0, c32=0))

    assert [x.numero for x in c.con_diferencia] == [32, 58]


# ── sin borrador de la DIAN ───────────────────────────────────────────────────────────────────────


def test_sin_borrador_de_la_dian_la_comparacion_no_esta_disponible() -> None:
    """Es el caso de quien declara por primera vez. No es un error."""
    c = comparar(_nuestras(c32=1_000), None)

    assert not c.disponible
    assert not c.casillas
    assert not c.coinciden, "sin nada con que comparar no se puede afirmar que coinciden"


def test_un_borrador_que_no_se_pudo_leer_tampoco_esta_disponible() -> None:
    """Un PDF cuya lectura no sacó ninguna casilla no sirve para comparar.

    Decir "coinciden" ahí sería la peor respuesta posible: afirmaría que el formulario está validado
    contra la DIAN cuando nadie lo comparó con nada.
    """
    vacio = DocumentReading(
        doc_type="SUGGESTED_RETURN",
        parser="renta210.pdf.v2",
        content_sha256="0" * 64,
        fields=[ExtractedField(name="anio_gravable", value=2025)],
    )

    c = comparar(_nuestras(c32=1_000), vacio)

    assert not c.disponible
    assert not c.coinciden


def test_los_campos_que_no_son_casillas_se_ignoran() -> None:
    """El lector saca también el año y el número de formulario, que no son cifras que comparar."""
    reading = DocumentReading(
        doc_type="SUGGESTED_RETURN",
        parser="renta210.pdf.v2",
        content_sha256="0" * 64,
        fields=[
            ExtractedField(name="anio_gravable", value=2025),
            ExtractedField(name="numero_formulario", value="141070249282"),
            ExtractedField(name="casilla_32", value=10_000_000, unit="COP"),
        ],
    )

    c = comparar(_nuestras(c32=10_000_000), reading)

    assert len(c.casillas) == 1
    assert c.coinciden


def test_cada_casilla_trae_los_dos_nombres() -> None:
    """El oficial para el contador y el de todos los días para el titular."""
    c = comparar(_nuestras(c32=1), _reading(c32=2))

    [casilla] = c.casillas
    assert casilla.nombre == "Ingresos brutos (rentas de trabajo)"
    assert casilla.en_palabras == "Lo que te pagaron como empleado"
