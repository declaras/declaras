"""Flujo de autenticacion en el Muisca (app Angular Material).

Distingue cuatro desenlaces, que son los que el agente necesita: autenticado, clave
rechazada, cuenta bloqueada y verificacion de identidad pendiente.

Proteccion importante: el portal deja el boton "Ingresar" deshabilitado hasta que el
formulario sea valido. Si despues de llenarlo el boton sigue deshabilitado, es que algo
cambio en la pagina y **no se envia nada**, para no gastar uno de los dos intentos que
nos permitimos antes de que la DIAN bloquee la cuenta.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from declaras.adapters.dian.browser import assert_portal_healthy, goto, is_visible
from declaras.adapters.dian.selectors import PORTAL_ID_CODES, SELECTORS
from declaras.domain.errors import (
    DianAccountLockedError,
    DianInvalidCredentialsError,
    DianLayoutChangedError,
    DianTimeoutError,
)
from declaras.domain.models import ChallengeKind, DianCredentials, IdentityChallenge
from declaras.observability import get_logger

log = get_logger(__name__)

_APP_SETTLE_MS = 2_500
_POST_SUBMIT_MS = 6_000


@dataclass
class LoginOutcome:
    authenticated: bool
    challenge: IdentityChallenge | None = None


async def perform_login(
    page: Page,
    *,
    base_url: str,
    credentials: DianCredentials,
    challenge_ttl_s: int,
) -> LoginOutcome:
    """Llena el formulario, verifica que el portal lo acepte y clasifica el resultado."""
    await fill_login_form(page, base_url=base_url, credentials=credentials)
    await _submit(page)

    return await _classify_outcome(page, challenge_ttl_s=challenge_ttl_s)


async def _classify_outcome(page: Page, *, challenge_ttl_s: int) -> LoginOutcome:
    """Decide en que quedo el login.

    La senal primaria es la URL: un login exitoso aterriza en el dashboard. Es mucho mas
    estable que buscar textos, que cambian con el idioma y el rediseno del portal.
    """
    sel = SELECTORS.login

    if sel.dashboard_path.lower() in page.url.lower():
        log.info("dian.login.success", url=page.url)
        return LoginOutcome(authenticated=True)

    if await is_visible(page, sel.locked_marker):
        raise DianAccountLockedError()

    challenge = await _read_challenge(page, challenge_ttl_s=challenge_ttl_s)
    if challenge is not None:
        log.info("dian.login.challenge_required", kind=challenge.kind.value)
        return LoginOutcome(authenticated=False, challenge=challenge)

    # Marcador de texto como respaldo, por si el portal cambia la ruta del dashboard.
    if await is_visible(page, sel.authenticated_marker, timeout_ms=4_000):
        log.info("dian.login.success", url=page.url, via="text_marker")
        return LoginOutcome(authenticated=True)

    if await is_visible(page, sel.error_banner):
        message = (await _text_of(page, sel.error_banner)) or ""
        log.warning("dian.login.rejected", portal_message=message[:160])
        raise DianInvalidCredentialsError(portal_message=message[:160])

    raise DianLayoutChangedError(
        "no se reconocio el resultado del login",
        selector="login.dashboard_path",
        url=page.url,
    )


async def fill_login_form(page: Page, *, base_url: str, credentials: DianCredentials) -> None:
    """Navega y llena el formulario, dejandolo listo para enviar, sin enviarlo.

    Se expone aparte del envio por dos razones: permite ensayar la interaccion contra el
    portal real sin gastar un intento de login, y mantiene `perform_login` legible.
    Termina verificando que el portal habilito el boton "Ingresar".
    """
    sel = SELECTORS.login
    await goto(page, f"{base_url.rstrip('/')}{sel.path}")
    await page.wait_for_timeout(_APP_SETTLE_MS)  # la SPA tarda en montar

    await _select_scope(page, credentials)
    await _select_id_kind(page, credentials)
    await _fill(page, sel.id_number, credentials.id_number, name="login.id_number")
    await _fill(page, sel.password, credentials.password.get_secret_value(), name="login.password")
    await _accept_data_consent(page)
    await _assert_submit_enabled(page)


async def resolve_challenge(page: Page, answers: list[str]) -> LoginOutcome:
    """Envia la respuesta del contribuyente y reevalua el estado de la sesion."""
    sel = SELECTORS.login
    try:
        inputs = page.locator(sel.challenge_input)
        count = await inputs.count()
        if count == 0:
            raise DianLayoutChangedError(
                "no se encontro el campo del reto", selector="login.challenge_input"
            )
        for index, answer in enumerate(answers[:count]):
            await inputs.nth(index).fill(answer)
        await page.locator(sel.challenge_submit).first.click()
        await page.wait_for_timeout(_POST_SUBMIT_MS)
    except PlaywrightTimeout as exc:
        raise DianTimeoutError("el portal no respondio al enviar el reto") from exc

    await assert_portal_healthy(page)

    outcome = await _classify_outcome(page, challenge_ttl_s=0)
    if outcome.authenticated:
        log.info("dian.challenge.accepted")
    return outcome


# ─────────────────────────── pasos del formulario ───────────────────────────


async def _select_scope(page: Page, credentials: DianCredentials) -> None:
    """Elige la pestana: a nombre propio o representando a un tercero."""
    sel = SELECTORS.login
    target = sel.scope_third_party if credentials.on_behalf_of_nit else sel.scope_own
    try:
        await page.locator(target).first.click()
        await page.wait_for_timeout(600)
    except (PlaywrightTimeout, PlaywrightError) as exc:
        raise DianLayoutChangedError(
            "no se encontro la pestana de tipo de acceso", selector="login.scope"
        ) from exc


async def _select_id_kind(page: Page, credentials: DianCredentials) -> None:
    """El tipo de documento es un mat-select: hay que abrirlo y elegir en el overlay."""
    sel = SELECTORS.login
    code = PORTAL_ID_CODES.get(credentials.id_kind.value)
    if code is None:
        raise DianLayoutChangedError(
            f"tipo de documento sin equivalente en el portal: {credentials.id_kind.value}"
        )
    try:
        await page.locator(sel.id_kind_trigger).first.click()
        await page.wait_for_timeout(800)
        await page.locator(f"{sel.id_kind_option}[value='{code}']").first.click()
        await page.wait_for_timeout(400)
    except (PlaywrightTimeout, PlaywrightError) as exc:
        raise DianLayoutChangedError(
            "no se pudo elegir el tipo de documento", selector="login.id_kind_trigger"
        ) from exc


async def _accept_data_consent(page: Page) -> None:
    """Marca el consentimiento de tratamiento de datos: sin el, el boton no se habilita."""
    sel = SELECTORS.login
    try:
        checkbox = page.locator(sel.data_consent_input).first
        if await checkbox.is_checked():
            return
        # Angular Material esconde el input real: se hace click sobre el componente.
        await page.locator(sel.data_consent).first.click()
        await page.wait_for_timeout(300)
        if not await checkbox.is_checked():
            await checkbox.check(force=True)
    except (PlaywrightTimeout, PlaywrightError) as exc:
        raise DianLayoutChangedError(
            "no se pudo aceptar el tratamiento de datos", selector="login.data_consent"
        ) from exc


async def _assert_submit_enabled(page: Page) -> None:
    """Red de seguridad: si el portal no habilita el boton, no se envia nada.

    Evita quemar un intento de login por un formulario mal llenado.
    """
    sel = SELECTORS.login
    button = page.locator(sel.submit).first
    try:
        enabled = await button.is_enabled(timeout=3_000)
    except (PlaywrightTimeout, PlaywrightError) as exc:
        raise DianLayoutChangedError(
            "no se encontro el boton Ingresar", selector="login.submit"
        ) from exc
    if not enabled:
        raise DianLayoutChangedError(
            "el portal no habilito el boton Ingresar: el formulario quedo incompleto y "
            "no se envio nada para no gastar un intento de login",
            selector="login.submit",
        )


async def _submit(page: Page) -> None:
    try:
        await page.locator(SELECTORS.login.submit).first.click()
        await page.wait_for_timeout(_POST_SUBMIT_MS)
    except PlaywrightTimeout as exc:
        raise DianTimeoutError("el portal no respondio al enviar el login") from exc
    await assert_portal_healthy(page)


async def _fill(page: Page, selector: str, value: str, *, name: str) -> None:
    try:
        await page.locator(selector).first.fill(value)
    except (PlaywrightTimeout, PlaywrightError) as exc:
        raise DianLayoutChangedError(f"no se pudo llenar {name}", selector=name) from exc


async def _read_challenge(page: Page, *, challenge_ttl_s: int) -> IdentityChallenge | None:
    sel = SELECTORS.login
    if not await is_visible(page, sel.challenge_form, timeout_ms=2_500):
        return None
    prompt = (await _text_of(page, sel.challenge_prompt)) or "Verificacion de identidad requerida"
    options = await _all_texts(page, sel.challenge_options)
    kind = ChallengeKind.SECURITY_QUESTIONS if options else ChallengeKind.EMAIL_CODE
    now = datetime.now(UTC)
    return IdentityChallenge(
        kind=kind,
        prompt=prompt.strip(),
        options=[opt.strip() for opt in options if opt.strip()],
        issued_at=now,
        expires_at=now + timedelta(seconds=challenge_ttl_s),
    )


async def _text_of(page: Page, selector: str) -> str | None:
    try:
        return await page.locator(selector).first.inner_text(timeout=1_500)
    except (PlaywrightTimeout, PlaywrightError):
        return None


async def _all_texts(page: Page, selector: str) -> list[str]:
    try:
        return await page.locator(selector).all_inner_texts()
    except (PlaywrightTimeout, PlaywrightError):
        return []
