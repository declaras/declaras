"""Las primitivas JSF son la base de todos los flujos: si fallan, no se baja nada."""

from __future__ import annotations

from declaras.adapters.dian.rest import jsf

FORM = "vistaDashboard:frmDashboard"

HTML = f"""
<html><body>
<form id="otro:frm"><input type="hidden" name="otro:frm:basura" value="no" /></form>
<form id="{FORM}" method="post">
  <input type="hidden" name="{FORM}:hddAnioSel" value="" />
  <input type="hidden" name="com.sun.faces.VIEW" value="H4sIAAA" />
  <input type="hidden" name="{FORM}:_idcl" value="" />
  <input type="text" name="{FORM}:visible" value="se ignora" />
  <input type="image" id="{FORM}:btnConsultarRUT" name="{FORM}:btnConsultarRUT" />
</form>
<form id="vistaMenuUsuario:frmMenuUsuario">
  <a id="nodo-1" expandido="true" editable="false"
     onclick="javascript:ejecutarEvento_vistaMenuUsuario_frmMenuUsuario__id32('nodo-1','seleccionar')">
     Consultar&#160;documento&#160;Diligenciado</a>
  <a onclick="javascript:ejecutarEvento_x__id32('nodo-2','seleccionar')">
     Diligenciar / Presentar</a>
</form>
</body></html>
"""


def test_solo_toma_los_ocultos_del_formulario_pedido():
    campos = jsf.hidden_fields(HTML, FORM)
    assert f"{FORM}:hddAnioSel" in campos
    assert "com.sun.faces.VIEW" in campos, "el ViewState debe viajar"
    assert "otro:frm:basura" not in campos
    assert f"{FORM}:visible" not in campos, "los visibles no son campos ocultos"


def test_postback_de_boton_agrega_coordenadas():
    boton = f"{FORM}:btnConsultarRUT"
    cuerpo = jsf.build_postback(HTML, form_id=FORM, button_id=boton)
    assert cuerpo[f"{boton}.x"] and cuerpo[f"{boton}.y"]


def test_postback_de_enlace_usa_idcl():
    cuerpo = jsf.build_link_postback(HTML, form_id=FORM, link_id="mi:enlace")
    assert cuerpo[f"{FORM}:_idcl"] == "mi:enlace"


def test_postback_ajax_declara_el_componente_origen():
    cuerpo = jsf.build_ajax_postback(HTML, form_id=FORM, source_id="mi:boton")
    assert cuerpo["AJAXREQUEST"] == "_viewRoot"
    assert cuerpo["mi:boton"] == "mi:boton"


def test_los_nodos_del_menu_se_resuelven_por_etiqueta():
    nodos = jsf.menu_nodes(HTML)
    assert nodos["Diligenciar / Presentar"] == "nodo-2"
    assert any("Diligenciado" in etiqueta for etiqueta in nodos)


def test_postback_de_menu_copia_los_atributos_del_nodo():
    cuerpo = jsf.build_menu_postback(
        HTML,
        node_id="nodo-1",
        form_id="vistaMenuUsuario:frmMenuUsuario",
        node_field="n_id",
        event_field="n_event",
        expanded_field="n_exp",
        editable_field="n_edit",
        event="seleccionar",
    )
    assert cuerpo["n_id"] == "nodo-1"
    assert cuerpo["n_event"] == "seleccionar"
    assert cuerpo["n_exp"] == "true"
    assert cuerpo["n_edit"] == "false"


def test_reconoce_documentos_binarios():
    assert jsf.looks_like_pdf(b"%PDF-1.4 resto")
    assert not jsf.looks_like_pdf(b"<html>")


def test_lee_el_nombre_del_content_disposition():
    header = "attachment; filename=reporteExogena2025.xlsx"
    assert jsf.filename_from_disposition(header, "x.bin") == "reporteExogena2025.xlsx"
    assert jsf.filename_from_disposition(None, "x.bin") == "x.bin"
