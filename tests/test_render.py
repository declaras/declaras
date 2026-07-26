from declaras.caso import Contribuyente
from declaras.optimizador import optimizar
from declaras.parametros import cargar
from declaras.render import ORDEN_CASILLAS, borrador_html, casillas, memoria_markdown
from tests.golden.casos import g0, g1, g2, g3, g4, g5

P = cargar(2025)

INSUMOS_RLG = ["ING_NETOS_GENERAL", "COSTOS_ARRIENDOS", "APLICADO_40", "EXTRA_LIMITE"]
NOMBRE_HOSTIL = '<script>alert(1)</script> Pérez & Cía "SAS"'


def _liq():
    return optimizar(g1(), P).liquidacion


def test_casillas_ordenadas_y_completas():
    filas = casillas(_liq())
    codigos = [f["codigo"] for f in filas]
    assert codigos.index("ING_BRUTO_GENERAL") < codigos.index("RLG_GENERAL") \
        < codigos.index("IMPUESTO_NETO") < codigos.index("SALDO")
    saldo = next(f for f in filas if f["codigo"] == "SALDO")
    assert saldo["valor"] == -6_504_023


def test_casillas_no_pierde_ni_duplica_nodos():
    """Un nodo del motor ausente de ORDEN_CASILLAS se caería del render en silencio."""
    assert len(set(ORDEN_CASILLAS)) == len(ORDEN_CASILLAS)
    for caso in (g0(), g1(), g2(), g3(), g4(), g5()):
        liq = optimizar(caso, P).liquidacion
        filas = casillas(liq)
        codigos = [f["codigo"] for f in filas]
        assert set(liq.nodos) - set(codigos) == set(), caso.contribuyente.nombre
        assert len(codigos) == len(liq.nodos), caso.contribuyente.nombre


def test_casillas_llevan_insumos():
    """La mitad 'insumos' de la trazabilidad del spec tiene que llegar al contador."""
    fila = next(f for f in casillas(_liq()) if f["codigo"] == "RLG_GENERAL")
    assert fila["insumos"] == INSUMOS_RLG


def test_memoria_incluye_formulas_y_flags():
    md = memoria_markdown(_liq(), g1())
    assert "RLG_GENERAL" in md and "62,154,472" in md
    assert "min(" in md  # las fórmulas viajan
    assert "G1 Asalariado" in md


def test_memoria_lista_insumos_solo_cuando_existen():
    md = memoria_markdown(_liq(), g1())
    assert f"**Insumos:** {', '.join(INSUMOS_RLG)}" in md
    for linea in md.splitlines():  # OBLIGADO_DECLARAR no tiene insumos: sin línea vacía
        if linea.startswith("**Insumos:**"):
            assert linea.removeprefix("**Insumos:**").strip()


def test_flags_del_motor_se_renderizan():
    """El render es genérico sobre liq.flags: sirve para cualquier código."""
    liq = optimizar(g2(), P).liquidacion
    assert liq.tiene_flag("COMPONENTE_INFLACIONARIO_PROVISIONAL")
    md = memoria_markdown(liq, g2())
    assert "## Alertas" in md
    assert "**[advertencia] COMPONENTE_INFLACIONARIO_PROVISIONAL**" in md
    html = borrador_html(liq, g2())
    assert 'class="flag"' in html and "COMPONENTE_INFLACIONARIO_PROVISIONAL" in html


def test_html_imprimible():
    html = borrador_html(_liq(), g1())
    assert "<table" in html and "IMPUESTO_NETO" in html
    assert 'class="neg"' in html  # el saldo a favor de G1 va marcado
    assert "http://" not in html and "https://" not in html  # autocontenido


def test_html_muestra_insumos():
    html = borrador_html(_liq(), g1())
    assert f"<small>Insumos: {', '.join(INSUMOS_RLG)}</small>" in html


def test_html_escapa_el_nombre_del_contribuyente():
    """El nombre llega de extracción LLM y del API: dato no confiable en HTML."""
    caso = g1().model_copy(update={
        "contribuyente": Contribuyente(num_doc="99", nombre=NOMBRE_HOSTIL)})
    html = borrador_html(optimizar(caso, P).liquidacion, caso)
    assert NOMBRE_HOSTIL not in html
    assert "<script>" not in html and "</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Pérez &amp; Cía &#34;SAS&#34;" in html  # tildes intactas, metacaracteres no


def test_autoescape_no_toca_formulas_ni_separador_de_miles():
    html = borrador_html(_liq(), g1())
    assert "110,400,000 − costos 0 − aplicado 44,160,000 − extra 4,085,528" in html
    assert "min(40% × 110,400,000, 1,340 UVT = 66,730,660)" in html
    assert "Σ max(0, mesada_mes agregada entre pagadores − 1,000 UVT" in html
    assert '<td class="v">62,154,472</td>' in html  # {:,} intacto
