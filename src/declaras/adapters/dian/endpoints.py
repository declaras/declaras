"""Rutas y nombres de campo del portal, para el conector HTTP.

CALIBRADO contra el portal real el 2026-07-25. Separado de selectors.py a proposito:
alli viven selectores del DOM (para el navegador) y aqui contratos HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class DianEndpoints:
    """Rutas del Muisca usadas por el conector HTTP."""

    # Entrada clasica: redirige a la app Angular y entrega el ideRequest en la URL.
    login_entry: str = "/WebArquitectura/DefLogin.faces"
    # Servicio que recibe las credenciales y siembra las cookies de sesion.
    weblogin: str = "/IdentidadRest_Acceso/api/sts/v1/auth/weblogin"
    # Portal autenticado: es el formulario JSF desde donde se piden los documentos.
    dashboard: str = "/WebDashboard/DefDashboard.faces"

    login_app_path: str = "/WebIdentidadLogin/"


@dataclass(frozen=True)
class DashboardForm:
    """Formulario JSF del dashboard y sus botones (input[type=image]).

    Un postback se arma con los campos ocultos del formulario mas las coordenadas del
    boton, en la forma `<id>.x` y `<id>.y`.
    """

    form_id: str = "vistaDashboard:frmDashboard"

    rut_copy: str = "vistaDashboard:frmDashboard:btnConsultarRUT"
    exogena: str = "vistaDashboard:frmDashboard:btnExogena"
    einvoices: str = "vistaDashboard:frmDashboard:btnFE"
    form_210: str = "vistaDashboard:frmDashboard:btnformulario210"
    file_and_submit: str = "vistaDashboard:frmDashboard:btnDiligenciarPresentar"
    obligations: str = "vistaDashboard:frmDashboard:btnObligacion"

    # Marcador de sesion valida: solo aparece autenticado.
    authenticated_marker: str = "btnConsultarRUT"


@dataclass(frozen=True)
class YearModal:
    """Un tramite que se pide eligiendo el anio en una ventana modal.

    El portal usa la misma mecanica para exogena y para facturas electronicas: se abre el
    modal, se registra el anio en un envio propio y se dispara un enlace que entrega el
    archivo. Describirlos con la misma forma permite un solo flujo para ambos.
    """

    open_button: str
    year_select: str
    year_hidden: str
    action_link: str
    min_year: int
    fallback_filename: str


@dataclass(frozen=True)
class MenuTree:
    """Menu lateral del portal, que es un arbol JSF.

    Un item no navega por URL: se escribe el id de su nodo en cuatro campos ocultos del
    formulario del menu y se envia. Los ids de nodo (id1641, id5043...) no se codifican
    aqui a proposito: se resuelven en tiempo de ejecucion por la etiqueta visible, que es
    estable, mientras los ids podrian cambiar entre usuarios o versiones del portal.
    """

    form_id: str = "vistaMenuUsuario:frmMenuUsuario"
    node_field: str = "vistaMenuUsuario_frmMenuUsuario__id32_id"
    event_field: str = "vistaMenuUsuario_frmMenuUsuario__id32_eventType"
    expanded_field: str = "vistaMenuUsuario_frmMenuUsuario__id32_expandido"
    editable_field: str = "vistaMenuUsuario_frmMenuUsuario__id32_editable"
    select_event: str = "seleccionar"

    # Etiquetas de los items que nos interesan.
    label_filed_document: str = "Consultar documento Diligenciado"
    label_file_and_submit: str = "Diligenciar / Presentar"


MENU = MenuTree()

# La app de diligenciamiento y presentacion es Angular, no el JSF clasico: el menu
# redirige a WebDilGestorFormularios. Ahi vivira el presentador del formulario 210.
FILING_APP_PATH = "/WebDilGestorFormularios/"


EXOGENA_MODAL = YearModal(
    open_button="vistaDashboard:frmDashboard:btnExogena",
    year_select="vistaDashboard:frmDashboard:anioSel",
    year_hidden="vistaDashboard:frmDashboard:hddAnioSel",
    action_link="vistaDashboard:frmDashboard:lnkDescargarReporteExogena",
    # Anios anteriores se sirven por el servlet de reportes, aun no implementado.
    min_year=2023,
    fallback_filename="exogena-{year}.xlsx",
)

EINVOICE_MODAL = YearModal(
    open_button="vistaDashboard:frmDashboard:btnFE",
    year_select="vistaDashboard:frmDashboard:anioSelFE",
    year_hidden="vistaDashboard:frmDashboard:hddAnioSelFE",
    action_link="vistaDashboard:frmDashboard:lnkConsultarFE",
    min_year=2023,
    fallback_filename="facturas-electronicas-{year}.xlsx",
)


@dataclass(frozen=True)
class DianApiEndpoints:
    """API REST de la DIAN, descubierta el 2026-07-25.

    El `client_id` es el de la aplicacion de identidad del portal y viaja en el header
    `clientid`, que la API exige incluso con un token valido.
    """

    base_url: str = "https://api.dian.gov.co"
    client_id: str = "Wo0aKAlB7vRP_16frPI1x9ZphBEa"

    # Identidad.
    token_from_cookies: str = "/identidad/sts/v2/cookies/token"
    userinfo: str = "/identidad/sts/v1/tokens/userinfo"

    # Renta de personas naturales (formulario 210).
    renta_years: str = "/documentos/renta210ingreso/v1/anios"
    renta_form_versions: str = "/documentos/renta210ingreso/v1/formatos/210/versionesRuta"
    renta_forms: str = "/documentos/renta210ingreso/v1/formularios"
    rut_registration_date: str = "/documentos/renta210ingreso/v1/contribuyente/fechaInscripcionRut"

    # RUT.
    economic_activities: str = "/rut/v10/contribuyentes/{document}/actividadeseconomicas"
    taxpayer_kind: str = "/rut/v10/contribuyentes/{document}/tipocontribuyente"

    # Estados con que se consulta el listado de formularios.
    state_filed: str = "presentado"
    state_pending: str = "pendiente"


DIAN_API = DianApiEndpoints()

ENDPOINTS = DianEndpoints()
DASHBOARD_FORM = DashboardForm()

# El scope del login: 2 = a nombre propio.
SCOPE_OWN_BEHALF = "2"
PORTAL_ID_CODES: dict[str, str] = {"CC": "CC", "CE": "CE", "PA": "PS", "NIT": "NIT"}
