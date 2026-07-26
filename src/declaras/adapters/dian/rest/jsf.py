"""Utilidades para conversar con el Muisca, que es un portal JSF.

Nada en el portal navega por URL: todo ocurre reenviando el formulario. Hay tres formas
de originar una accion, y aqui esta una primitiva por cada una:

  build_postback        boton de imagen: se envian las coordenadas del clic
  build_ajax_postback   boton dentro de una ventana modal (Ajax4jsf)
  build_link_postback   enlace: escribe su id en el campo oculto `_idcl`
  build_menu_postback   item del menu lateral: escribe el id del nodo del arbol

Sobre todas ellas se componen los flujos de cada documento.
"""

from __future__ import annotations

import re

_INPUT_RE = re.compile(r"<input[^>]*>", re.IGNORECASE)
_HIDDEN_RE = re.compile(r'type\s*=\s*["\']hidden', re.IGNORECASE)
_NAME_RE = re.compile(r'name\s*=\s*["\']([^"\']+)')
_VALUE_RE = re.compile(r'value\s*=\s*["\']([^"\']*)')

# Coordenadas del clic dentro del icono: cualquier punto dentro del boton sirve.
_CLICK_X = "36"
_CLICK_Y = "36"

# Marcas del protocolo JSF del portal.
_AJAX_VIEW_ROOT = "_viewRoot"
_LINK_CLICKED_FIELD = "_idcl"
_MENU_ANCHOR_RE = re.compile(r"<a([^>]*)>(.*?)</a>", re.DOTALL)
_MENU_EVENT_RE = re.compile(r"ejecutarEvento_\w+\('([^']+)'")


def hidden_fields(html: str, form_id: str) -> dict[str, str]:
    """Devuelve los campos ocultos del formulario indicado."""
    match = re.search(
        rf'<form[^>]*(?:id|name)="{re.escape(form_id)}"[^>]*>(.*?)</form>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    scope = match.group(1) if match else html

    fields: dict[str, str] = {}
    for tag in _INPUT_RE.findall(scope):
        if not _HIDDEN_RE.search(tag):
            continue
        name = _NAME_RE.search(tag)
        if not name:
            continue
        value = _VALUE_RE.search(tag)
        fields[name.group(1)] = value.group(1) if value else ""
    return fields


def build_postback(html: str, *, form_id: str, button_id: str) -> dict[str, str]:
    """Arma el cuerpo del postback que equivale a pulsar un boton de imagen."""
    payload = hidden_fields(html, form_id)
    payload[f"{button_id}.x"] = _CLICK_X
    payload[f"{button_id}.y"] = _CLICK_Y
    return payload


def build_ajax_postback(
    html: str, *, form_id: str, source_id: str, extra: dict[str, str] | None = None
) -> dict[str, str]:
    """Arma el cuerpo de un submit Ajax4jsf (A4J.AJAX.Submit).

    El portal usa este mecanismo en los botones dentro de ventanas modales. La convencion
    de Ajax4jsf es marcar la peticion con `AJAXREQUEST` y declarar el componente que la
    origina repitiendo su id como nombre y valor.
    """
    payload = hidden_fields(html, form_id)
    payload.update(extra or {})
    payload["AJAXREQUEST"] = _AJAX_VIEW_ROOT
    payload[source_id] = source_id
    return payload


def build_link_postback(
    html: str, *, form_id: str, link_id: str, extra: dict[str, str] | None = None
) -> dict[str, str]:
    """Arma el cuerpo equivalente a pulsar un enlace JSF.

    Los enlaces del portal no navegan: escriben su id en el campo oculto `_idcl` del
    formulario y lo envian. Esto replica exactamente ese comportamiento.
    """
    payload = hidden_fields(html, form_id)
    payload.update(extra or {})
    payload[f"{form_id}:{_LINK_CLICKED_FIELD}"] = link_id
    return payload


def menu_nodes(html: str) -> dict[str, str]:
    """Mapea la etiqueta visible de cada item del menu lateral a su id de nodo.

    El id no esta en el atributo `id` del enlace sino en el argumento del onclick, asi
    que se lee de ahi. Resolver por etiqueta evita codificar ids que pueden cambiar.
    """
    nodes: dict[str, str] = {}
    for match in _MENU_ANCHOR_RE.finditer(html):
        attrs, body = match.group(1), match.group(2)
        node = _MENU_EVENT_RE.search(attrs)
        if node is None:
            continue
        label = re.sub(r"<[^>]+>", "", body).replace("\xa0", " ").strip()
        if label:
            nodes[label] = node.group(1)
    return nodes


def build_menu_postback(
    html: str,
    *,
    node_id: str,
    form_id: str,
    node_field: str,
    event_field: str,
    expanded_field: str,
    editable_field: str,
    event: str,
) -> dict[str, str]:
    """Arma el cuerpo equivalente a pulsar un item del menu lateral."""
    payload = hidden_fields(html, form_id)
    payload[node_field] = node_id
    payload[event_field] = event
    payload[expanded_field] = _node_attribute(html, node_id, "expandido")
    payload[editable_field] = _node_attribute(html, node_id, "editable")
    return payload


def _node_attribute(html: str, node_id: str, attribute: str) -> str:
    """Lee un atributo del nodo del menu, como hace el JavaScript del portal."""
    match = re.search(rf'<a[^>]*id="{re.escape(node_id)}"[^>]*>', html)
    if match is None:
        return "false"
    value = re.search(rf'{attribute}="([^"]*)"', match.group(0))
    return value.group(1) if value else "false"


def looks_like_pdf(content: bytes) -> bool:
    return content[:4] == b"%PDF"


def filename_from_disposition(header: str | None, fallback: str) -> str:
    """Extrae el nombre de archivo de un Content-Disposition."""
    if not header:
        return fallback
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', header, re.IGNORECASE)
    return match.group(1).strip() if match else fallback
