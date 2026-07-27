"""Dobles del cliente de Anthropic, compartidos por las pruebas de extracción.

Vive aparte porque lo usan dos suites: la del extractor del 220 (`test_extractor_220.py`,
que ejercita sus guards) y la del lector que lo envuelve para el registry
(`test_lectura_certificado.py`). Con una copia en cada archivo, un cambio en el contrato
del SDK se arregla en una y se olvida en la otra.

`stop_reason` es parámetro a propósito: la ausencia de salida estructurada (refusal,
max_tokens) es una de las fallas que el extractor tiene que reportar, y solo se puede
ejercitar desde el doble.
"""

from __future__ import annotations

from typing import Any


class RespuestaFalsa:
    def __init__(self, parsed: Any, stop_reason: str = "end_turn") -> None:
        self.parsed_output = parsed
        self.stop_reason = stop_reason


class MessagesFalso:
    def __init__(self, parsed: Any, stop_reason: str = "end_turn") -> None:
        self._parsed = parsed
        self._stop_reason = stop_reason
        self.llamadas: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> RespuestaFalsa:
        self.llamadas.append(kwargs)
        return RespuestaFalsa(self._parsed, self._stop_reason)


class ClienteFalso:
    def __init__(self, parsed: Any, stop_reason: str = "end_turn") -> None:
        self.messages = MessagesFalso(parsed, stop_reason)


class MessagesQueRevienta:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def parse(self, **_kwargs: Any) -> Any:
        raise self._error


class ClienteQueRevienta:
    """Cliente cuya request falla, como falla el SDK de verdad.

    Sirve para la falla mas probable en un demo y la que peor se degrada: sin
    `ANTHROPIC_API_KEY` el cliente SE CONSTRUYE bien y revienta al hacer la request con un
    `TypeError` ("Could not resolve authentication method"), que no es `ValueError` y no se
    parece en nada a un documento ilegible.
    """

    def __init__(self, error: Exception) -> None:
        self.messages = MessagesQueRevienta(error)
