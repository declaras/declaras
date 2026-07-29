from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from declaras.services.conciliacion import Respuesta

AHORA = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


def test_un_no_es_una_respuesta_que_se_guarda():
    """El punto del modelo: `tiene=False` persiste — sin esto el sistema pregunta por
    prepagada para siempre (las peticiones de T6 se apagan con la respuesta)."""
    r = Respuesta(pregunta="PREPAGADA", tiene=False, detalle={}, quien="cliente", cuando=AHORA)
    assert r.tiene is False
    revivida = Respuesta.model_validate(r.model_dump())
    assert revivida == r


def test_el_detalle_viaja_con_la_respuesta():
    r = Respuesta(
        pregunta="DEPENDIENTES",
        tiene=True,
        detalle={"cuantos": 2, "tipo": "hijo_menor"},
        quien="cliente",
        cuando=AHORA,
    )
    assert r.detalle["cuantos"] == 2


def test_una_clave_desconocida_revienta():
    """`extra="forbid"`: un typo del API no puede perderse en silencio."""
    with pytest.raises(ValidationError):
        Respuesta(
            pregunta="PREPAGADA",
            tiene=False,
            detalle={},
            quien="cliente",
            cuando=AHORA,
            respuesta="si",
        )
