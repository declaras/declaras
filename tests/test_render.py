import pytest

from declaras.caso import Contribuyente, IngresoLaboral
from declaras.motor import Flag
from declaras.optimizador import optimizar
from declaras.parametros import cargar
from declaras.render import ORDEN_CASILLAS, borrador_html, casillas, memoria_markdown
from tests.golden.casos import FX, g0, g1, g2, g3, g4, g5

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


def test_memoria_separa_valor_como_e_insumos_en_parrafos():
    """Sin línea en blanco, el soft-break de CommonMark corre los tres en uno."""
    md = memoria_markdown(_liq(), g1())
    i = md.index("## RLG_GENERAL")
    bloque = md[i:md.index("## RLG_PENSIONES")]
    assert "**Valor:** 62,154,472\n\n**Cómo:**" in bloque
    assert "\n\n**Insumos:**" in bloque


def test_memoria_de_g5_audita_los_cinco_terminos_del_saldo():
    """G5 usa anticipo pagado y saldo a favor: sin ellos el saldo es inauditable."""
    md = memoria_markdown(optimizar(g5(), P).liquidacion, g5())
    assert "2,000,000" in md and "500,000" in md
    assert ("impuesto neto 12,928,640 + anticipo siguiente 4,732,160 "
            "− retenciones 1,000,000 − anticipo pagado 2,000,000 "
            "− saldo a favor anterior 500,000") in md


def test_memoria_escapa_identidad_del_contribuyente():
    """El nombre es dato no confiable: no puede fabricar estructura Markdown."""
    hostil = 'Ana\n## SALDO — Saldo a pagar (+) o a favor (−)\n**Valor:** 999 <img src=x>'
    caso = g1().model_copy(update={
        "contribuyente": Contribuyente(num_doc="9\n# 8", nombre=hostil)})
    md = memoria_markdown(optimizar(caso, P).liquidacion, caso)
    # El nombre entero cabe en la primera línea: los \n internos se volvieron espacio
    # (nombre y num_doc). Sin el colapso, "999" y el doc caerían en líneas aparte.
    primera = md.split("\n", 1)[0]
    assert primera.endswith(r"999 \<img src=x\> (CC 9 \# 8)")
    assert r"Ana \#\# SALDO" in primera
    assert md.count("## SALDO") == 1  # la sección falsa no existe
    assert "**Valor:** 999" not in md
    assert r"\<img src=x\>" in md                    # el HTML llegó escapado
    assert "<" not in md.replace("\\<", "")          # y ninguno quedó crudo


def test_memoria_escapa_los_mensajes_de_flag():
    """Los mensajes de flag llevan nombres de terceros: empleador, pagador, entidad."""
    hostil = 'ACME\n## SALDO — Saldo a pagar (+) o a favor (−)\n**Valor:** 999'
    caso = g1().model_copy(update={"laborales": [IngresoLaboral(
        empleador_nit="900111222", empleador_nombre=hostil, salarios=1_000_000,
        aportes_salud=0, aportes_pension=0, retencion=5_000_000, fuente=FX)]})
    liq = optimizar(caso, P).liquidacion
    assert liq.tiene_flag("RETENCION_EXCEDE_INGRESO")  # retención 5M > base 1M
    md = memoria_markdown(liq, caso)
    assert md.count("## SALDO") == 1
    assert "**Valor:** 999" not in md
    assert r"ACME \#\# SALDO" in md  # colapsado y escapado dentro de la alerta


def test_flags_del_motor_se_renderizan_con_severidad():
    """El render es genérico sobre liq.flags: sirve para cualquier código."""
    liq = optimizar(g2(), P).liquidacion
    assert liq.tiene_flag("COMPONENTE_INFLACIONARIO_PROVISIONAL")
    md = memoria_markdown(liq, g2())
    assert "## Alertas" in md
    assert "**[advertencia] COMPONENTE_INFLACIONARIO_PROVISIONAL**" in md
    html = borrador_html(liq, g2())
    assert 'class="flag advertencia"' in html
    assert "[advertencia] COMPONENTE_INFLACIONARIO_PROVISIONAL" in html


def test_html_distingue_las_tres_severidades():
    """La severidad viaja en clase CSS y en texto (para impresión en B/N)."""
    liq_info = optimizar(g4(), P).liquidacion
    assert liq_info.tiene_flag("NO_OBLIGADO")
    html = borrador_html(liq_info, g4())
    assert 'class="flag info"' in html and "[info] NO_OBLIGADO" in html

    liq_bloq = liq_info.model_copy(update={"flags": [
        Flag(codigo="X_BLOQUEANTE", mensaje="no presentar", severidad="bloqueante")]})
    html = borrador_html(liq_bloq, g4())
    assert 'class="flag bloqueante"' in html and "[bloqueante] X_BLOQUEANTE" in html
    for severidad in ("info", "advertencia", "bloqueante"):
        assert f".flag.{severidad}{{" in html  # las tres clases están definidas


def test_obligado_declarar_se_lee_si_no():
    """'Valor: 1' no es auditable por un contador; el int sigue ahí para el API."""
    fila = next(f for f in casillas(_liq()) if f["codigo"] == "OBLIGADO_DECLARAR")
    assert fila["valor"] == 1 and fila["valor_texto"] == "Sí"
    assert "**Valor:** Sí" in memoria_markdown(_liq(), g1())
    assert '<td class="v">Sí</td>' in borrador_html(_liq(), g1())

    liq_no = optimizar(g4(), P).liquidacion  # G4 no supera ningún tope
    assert liq_no.valor("OBLIGADO_DECLARAR") == 0
    assert "**Valor:** No" in memoria_markdown(liq_no, g4())


def test_render_exige_liquidacion_y_caso_del_mismo_anio():
    liq = _liq()  # AG 2025
    otro_anio = g1().model_copy(update={"anio_gravable": 2026})
    for render in (memoria_markdown, borrador_html):
        with pytest.raises(ValueError, match="año gravable"):
            render(liq, otro_anio)


def test_html_imprimible():
    html = borrador_html(_liq(), g1())
    assert "<table" in html and "IMPUESTO_NETO" in html
    assert 'class="neg"' in html  # el saldo a favor de G1 va marcado
    assert "<thead>" in html and "<tbody>" in html
    assert "tr{break-inside:avoid}" in html            # ninguna casilla partida
    assert "thead{display:table-header-group}" in html  # encabezado por página
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
