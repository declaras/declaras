"""Dobles del cliente del proveedor de extracción, compartidos por las pruebas.

Vive aparte porque lo usan seis archivos de prueba: los guards de cada extractor, los lectores
que los envuelven para el registry, y la autodetección. Con una copia en cada uno, un cambio en
el contrato del SDK se arregla en uno y se olvida en los otros cinco — que es exactamente lo que
pasaría hoy, porque el proveedor cambió de Anthropic a Gemini y este archivo es, junto con
`extraccion/_base.py`, lo único que lo sabe.

DOS COSAS QUE EL DOBLE TIENE QUE PODER FINGIR, porque son fallas que el extractor reporta y no
se pueden ejercitar de otra forma:

- que NO haya salida estructurada (rechazo de los clasificadores, presupuesto agotado, error del
  proveedor) — `sin_salida_por`;
- que la request revient (sin llave, cuota agotada, proveedor caído) — `ClienteQueRevienta`.
"""

from __future__ import annotations

import json
from typing import Any


def _como_json(parsed: Any) -> str:
    """Lo que el proveedor devolvería como texto para esa extracción.

    Los casos pasan un modelo de pydantic —lo mismo que el extractor va a validar de vuelta— y
    alguno pasa un objeto suelto con los campos puestos a mano. Los dos tienen que servir: el
    doble finge el transporte, no el esquema.
    """
    if hasattr(parsed, "model_dump_json"):
        return str(parsed.model_dump_json())
    return json.dumps(vars(parsed), default=str)


class EstadoFalso:
    def __init__(self, message: str) -> None:
        self.message = message


class RespuestaFalsa:
    def __init__(self, parsed: Any, sin_salida_por: str | None = None) -> None:
        # Sin salida, el proveedor no devuelve texto y deja el motivo en el estado. Es el par
        # exacto que el guard de la base mira.
        self.output_text = "" if parsed is None else _como_json(parsed)
        self.status = EstadoFalso(sin_salida_por) if sin_salida_por else None


class InteractionsFalso:
    def __init__(self, parsed: Any, sin_salida_por: str | None = None) -> None:
        self._parsed = parsed
        self._sin_salida_por = sin_salida_por
        self.llamadas: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> RespuestaFalsa:
        self.llamadas.append(kwargs)
        return RespuestaFalsa(self._parsed, self._sin_salida_por)


class ClienteFalso:
    def __init__(self, parsed: Any, sin_salida_por: str | None = None) -> None:
        self.interactions = InteractionsFalso(parsed, sin_salida_por)


class InteractionsQueRevienta:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def create(self, **_kwargs: Any) -> Any:
        raise self._error


class ClienteQueRevienta:
    """Cliente cuya request falla, como falla el SDK de verdad.

    Sirve para la falla más probable en un demo y la que peor se degrada: sin `GEMINI_API_KEY` el
    cliente se construye y revienta al hacer la request con un error que NO es `ValueError` y no
    se parece en nada a un documento ilegible. Sin la rama que lo atrapa en la frontera, sube
    hasta el manejador genérico —500, no reintentable— y el certificado queda en el expediente
    sin lectura y sin alerta.
    """

    def __init__(self, error: Exception) -> None:
        self.interactions = InteractionsQueRevienta(error)
