"""Conector falso, deterministico, para desarrollo y pruebas.

Existe por dos razones concretas. Primera: el equipo que consume la API (el agente de
WhatsApp) puede integrarse hoy, sin esperar la calibracion del portal real. Segunda:
las ramas de error del Muisca son casi imposibles de provocar a voluntad, y aca se
disparan a pedido.

La rama se escoge con la clave enviada:
    contiene "bad"        -> DIAN_INVALID_CREDENTIALS
    contiene "locked"     -> DIAN_ACCOUNT_LOCKED
    contiene "down"       -> DIAN_PORTAL_UNAVAILABLE
    contiene "slow"       -> DIAN_PORTAL_TIMEOUT
    contiene "challenge"  -> reto de identidad (patron relevo); se resuelve con "1234"
    contiene "noexo"      -> exogena no publicada, el resto si baja
    cualquier otra        -> exito completo
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from declaras.domain.errors import (
    DianAccountLockedError,
    DianDocumentUnavailableError,
    DianIdentityChallengeError,
    DianInvalidCredentialsError,
    DianPortalUnavailableError,
    DianTimeoutError,
    ValidationError,
)
from declaras.domain.models import (
    ChallengeAnswer,
    ChallengeKind,
    DianCredentials,
    DocumentType,
    IdentityChallenge,
    RawDocument,
    TaxpayerRef,
)

_EXPECTED_CHALLENGE_ANSWER = "1234"
_PDF_STUB = b"%PDF-1.4\n%% documento de prueba declaras\n"


class FakeDianSession:
    """Sesion falsa que devuelve documentos sinteticos estables."""

    def __init__(self, *, scenario: str, challenge: IdentityChallenge | None) -> None:
        self.session_id = str(uuid4())
        self._scenario = scenario
        self._challenge = challenge
        self._closed = False

    @property
    def pending_challenge(self) -> IdentityChallenge | None:
        return self._challenge

    async def download(self, doc_type: DocumentType, taxpayer: TaxpayerRef) -> RawDocument:
        if self._challenge is not None:
            raise DianIdentityChallengeError(challenge=self._challenge.model_dump(mode="json"))
        if self._closed:
            raise ValidationError("La sesión ya fue cerrada.")
        if doc_type is DocumentType.EXOGENA and "noexo" in self._scenario:
            raise DianDocumentUnavailableError(
                "La DIAN todavía no publica la información exógena del periodo.",
                doc_label="exogena",
            )
        return RawDocument(
            doc_type=doc_type,
            filename=f"{doc_type.value.lower()}-{taxpayer.tax_year}.pdf",
            content=_PDF_STUB + f"{doc_type.value}:{taxpayer.subject_key}".encode(),
            content_type="application/pdf",
            source_url=f"https://fake.local/{doc_type.value.lower()}",
            metadata={"fake": True, "tax_year": taxpayer.tax_year},
        )

    async def capture_evidence(self, label: str) -> RawDocument:
        return RawDocument(
            doc_type=DocumentType.EVIDENCE,
            filename=f"{label}.png",
            content=b"\x89PNG\r\n\x1a\n fake",
            content_type="image/png",
            metadata={"label": label, "fake": True},
        )

    async def answer_challenge(self, answer: ChallengeAnswer) -> None:
        if self._challenge is None:
            raise ValidationError("La sesión no tiene ninguna verificación pendiente.")
        if answer.answers[0].strip() != _EXPECTED_CHALLENGE_ANSWER:
            raise DianInvalidCredentialsError("La respuesta de verificación fue rechazada.")
        self._challenge = None

    async def close(self) -> None:
        self._closed = True


class FakeDianConnector:
    """Implementa DianConnector sin tocar la red."""

    def __init__(self, *, challenge_ttl_s: int = 600) -> None:
        self._challenge_ttl_s = challenge_ttl_s

    async def open_session(
        self, credentials: DianCredentials, taxpayer: TaxpayerRef
    ) -> FakeDianSession:
        scenario = credentials.password.get_secret_value().lower()

        if "bad" in scenario:
            raise DianInvalidCredentialsError()
        if "locked" in scenario:
            raise DianAccountLockedError()
        if "down" in scenario:
            raise DianPortalUnavailableError()
        if "slow" in scenario:
            raise DianTimeoutError()

        challenge = None
        if "challenge" in scenario:
            now = datetime.now(UTC)
            challenge = IdentityChallenge(
                kind=ChallengeKind.EMAIL_CODE,
                prompt="Ingresa el codigo de 4 digitos que llego a tu correo",
                options=[],
                issued_at=now,
                expires_at=now + timedelta(seconds=self._challenge_ttl_s),
            )
        return FakeDianSession(scenario=scenario, challenge=challenge)

    async def shutdown(self) -> None:
        return None
