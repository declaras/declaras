"""Selectores y rutas del portal Muisca, centralizados en un solo archivo.

POR QUE ESTA TODO AQUI: el Muisca cambia sin avisar y no tiene API. Cuando el portal
cambie, la reparacion debe ser editar este archivo y nada mas. Ningun selector debe
aparecer suelto en los flujos.

ESTADO: los valores son la mejor aproximacion documentada del portal y estan
PENDIENTES DE CALIBRACION contra una sesion real (hito 1). Cada campo lleva el nombre
logico que se reporta en DianLayoutChangedError, de modo que cuando algo se rompa el
log diga exactamente que selector recalibrar.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LoginSelectors:
    """Login del Muisca: es una app Angular Material, no el JSF antiguo.

    CALIBRADO contra el portal real el 2026-07-25. La ruta .faces redirige a
    /WebIdentidadLogin/ y ahi vive el formulario. Ojo con dos cosas: el tipo de
    documento es un mat-select (no un <select> nativo) y el boton "Ingresar" nace
    deshabilitado hasta que el formulario sea valido, lo que usamos como red de
    seguridad para no gastar intentos de login.
    """

    path: str = "/WebArquitectura/DefLogin.faces"
    app_url_marker: str = "WebIdentidadLogin"

    # Pestanas de la izquierda: se eligen por texto.
    scope_own: str = "button:has-text('A nombre propio')"
    scope_third_party: str = "button:has-text('A nombre de un tercero')"

    # Tipo de documento: mat-select mas overlay de opciones.
    id_kind_trigger: str = "mat-select[name='tipoDocumento']"
    id_kind_option: str = "mat-option"

    id_number: str = "input[name='numDocumento']"
    password: str = "input[type='password']"
    password_reveal: str = "button:has-text('visibility')"

    # Checkbox obligatorio de tratamiento de datos: sin el, no se habilita el boton.
    data_consent: str = "mat-checkbox:has(input[name='aceptaTratamientoDatos'])"
    data_consent_input: str = "input[name='aceptaTratamientoDatos']"

    submit: str = "button:has-text('Ingresar')"

    forgot_password_link: str = "a:has-text('Olvidó su contraseña')"
    enable_account_link: str = "a:has-text('habilítela aquí')"

    error_banner: str = (
        ".mat-error, .error, .alert-danger, [class*='mensaje-error'], mat-snack-bar-container"
    )
    locked_marker: str = "text=/bloquead|deshabilitad|inactiv/i"

    challenge_form: str = "[class*='pregunta'], [class*='validacion'], [class*='codigo']"
    challenge_prompt: str = "[class*='pregunta'] label, [class*='validacion'] label, h2, h3"
    challenge_options: str = "mat-radio-button, [role='radio'], mat-option"
    challenge_input: str = "input[name*='codigo'], input[name*='respuesta'], input[type='tel']"
    challenge_submit: str = "button:has-text('Continuar'), button:has-text('Validar')"

    # CALIBRADO: al autenticar cae en WebDashboard/DefDashboard.faces, el portal JSF
    # clasico. El enlace de salida dice solo "Cerrar", no "cerrar sesion".
    dashboard_path: str = "/WebDashboard/DefDashboard.faces"
    # No se pueden unir motores text= con comas: se usa CSS :has-text.
    authenticated_marker: str = "body:has-text('Mis actividades'), body:has-text('A Nombre Propio')"


# Codigos que usa el portal en el mat-select de tipo de documento (calibrado).
PORTAL_ID_CODES: dict[str, str] = {
    "CC": "CC",
    "CE": "CE",
    "PA": "PS",
    "NIT": "NIT",
}


@dataclass(frozen=True)
class DashboardSelectors:
    """Puntos de entrada del dashboard, CALIBRADOS contra el portal real (2026-07-25).

    El Muisca es JSF: los enlaces visibles son href="#" y la navegacion ocurre por
    submit de formulario. Lo que si es clickeable y estable son estos input[type=image]
    (los iconos del dashboard), cuyos ids son semanticos. Por eso los flujos navegan
    haciendo clic aqui y no con page.goto de una ruta.
    """

    path: str = "/WebDashboard/DefDashboard.faces"

    rut_copy: str = 'input[id="vistaDashboard:frmDashboard:btnConsultarRUT"]'
    rut_update: str = 'input[id="vistaDashboard:frmDashboard:btnActualizarRUT"]'
    exogena: str = 'input[id="vistaDashboard:frmDashboard:btnExogena"]'
    einvoices: str = 'input[id="vistaDashboard:frmDashboard:btnFE"]'
    form_210: str = 'input[id="vistaDashboard:frmDashboard:btnformulario210"]'
    file_and_submit: str = 'input[id="vistaDashboard:frmDashboard:btnDiligenciarPresentar"]'
    obligations: str = 'input[id="vistaDashboard:frmDashboard:btnObligacion"]'
    payment_receipts: str = 'input[id="vistaDashboard:frmDashboard:btnPagoRecibos"]'
    reporters: str = 'input[id="vistaDashboard:frmDashboard:btnReportantesRub"]'


@dataclass(frozen=True)
class RutSelectors:
    path: str = "/WebRutMuisca/DefConsultaEstadoRUT.faces"
    print_button: str = "a[id*='imprimir'], input[id*='imprimir']"
    not_found_marker: str = "text=/no se encontr|sin informaci/i"


@dataclass(frozen=True)
class ExogenaSelectors:
    path: str = "/WebInformacionExogena/DefConsultaInformacionExogena.faces"
    year_select: str = "select[id*='anoGravable'], select[name*='anoGravable']"
    submit: str = "input[id*='consultar'], button[id*='consultar']"
    download_link: str = "a[id*='descargar'], a[href*='.zip'], a[href*='.xls']"
    empty_marker: str = "text=/no hay informaci|sin registros|no se encontr/i"


@dataclass(frozen=True)
class ReturnSelectors:
    """Declaracion presentada del anio anterior y declaracion sugerida."""

    path: str = "/WebArquitectura/DefConsultaDocumentos.faces"
    year_select: str = "select[id*='anoGravable']"
    form_select: str = "select[id*='formulario']"
    submit: str = "input[id*='consultar'], button[id*='consultar']"
    result_row: str = "table tbody tr"
    pdf_link: str = "a[id*='pdf'], a[href*='.pdf']"
    empty_marker: str = "text=/no hay documentos|sin resultados/i"
    suggested_path: str = "/WebRentaNaturales/DefDeclaracionSugerida.faces"


@dataclass(frozen=True)
class EInvoiceSelectors:
    """Micrositio de facturacion electronica: insumo de la deduccion del 1%."""

    path: str = "/WebFacturaElectronica/DefConsultaDocumentosRecibidos.faces"
    date_from: str = "input[id*='fechaInicial']"
    date_to: str = "input[id*='fechaFinal']"
    submit: str = "input[id*='consultar'], button[id*='consultar']"
    export_button: str = "a[id*='exportar'], button[id*='exportar']"
    total_label: str = "[id*='totalRegistros'], [id*='valorTotal']"
    empty_marker: str = "text=/no se encontraron|sin documentos/i"


@dataclass(frozen=True)
class MuiscaSelectors:
    """Punto unico de entrada a todos los selectores del portal."""

    login: LoginSelectors = field(default_factory=LoginSelectors)
    dashboard: DashboardSelectors = field(default_factory=DashboardSelectors)
    rut: RutSelectors = field(default_factory=RutSelectors)
    exogena: ExogenaSelectors = field(default_factory=ExogenaSelectors)
    returns: ReturnSelectors = field(default_factory=ReturnSelectors)
    einvoices: EInvoiceSelectors = field(default_factory=EInvoiceSelectors)

    # Marcadores globales de indisponibilidad del portal.
    maintenance_marker: str = "text=/en mantenimiento|no disponible|intente m.s tarde/i"
    session_expired_marker: str = "text=/sesi.n.*expir|vuelva a ingresar/i"


SELECTORS = MuiscaSelectors()
