"""Una clave de la DIAN nunca puede aparecer en un log ni en un repr."""

from __future__ import annotations

from declaras.domain.models import DianCredentials
from declaras.observability.logging import redact_processor


def test_repr_de_credenciales_no_expone_la_clave():
    creds = DianCredentials(id_number="1020304050", password="superSecreta123")
    rendered = f"{creds!r} {creds}"
    assert "superSecreta123" not in rendered
    assert "***" in rendered


def test_el_procesador_de_logs_enmascara_llaves_sensibles():
    event = {
        "event": "login",
        "password": "superSecreta123",
        "clave": "otra",
        "nested": {"dian_password": "x", "safe": "visible"},
        "answers": ["1234"],
    }
    out = redact_processor(None, "info", event)
    assert out["password"] == "***"
    assert out["clave"] == "***"
    assert out["nested"]["dian_password"] == "***"
    assert out["nested"]["safe"] == "visible"
    assert out["answers"] == "***"


def test_enmascara_secretstr_embebido_en_texto():
    out = redact_processor(None, "info", {"detail": "DianCredentials(password=SecretStr('abc'))"})
    assert "abc" not in out["detail"]
