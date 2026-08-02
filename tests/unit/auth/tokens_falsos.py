"""Un emisor falso de tokens, para poder probar el portero sin red y sin Supabase.

POR QUE FIRMA DE VERDAD Y NO MOCKEA `jwt.decode`. Mockear la verificacion probaria que el codigo
llama a una funcion, no que la verificacion FUNCIONA — y lo que hay que probar es justamente que
un token mal firmado se rechaza. Con una llave EC de verdad, cada caso de abajo es el ataque real:
se firma algo y se mira si pasa.
"""

from __future__ import annotations

import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from declaras.api.auth.jwks import CacheDeLlaves

EMISOR = "https://proyecto.supabase.co/auth/v1"
CORREO = "contador@declaras.co"


class EmisorFalso:
    """Firma tokens como lo haria Supabase, con su propia llave EC."""

    def __init__(self, kid: str = "llave-1") -> None:
        self.kid = kid
        self._privada = ec.generate_private_key(ec.SECP256R1())

    @property
    def jwk_publica(self) -> dict[str, Any]:
        publica = jwt.algorithms.ECAlgorithm.to_jwk(self._privada.public_key(), as_dict=True)
        return {**publica, "kid": self.kid, "alg": "ES256", "use": "sig"}

    @property
    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.jwk_publica]}

    def token(
        self,
        *,
        email: str | None = CORREO,
        sub: str = "usuario-1",
        emisor: str = EMISOR,
        audiencia: str = "authenticated",
        vence_en: int = 3600,
        kid: str | None = None,
        claims_extra: dict[str, Any] | None = None,
    ) -> str:
        ahora = int(time.time())
        claims: dict[str, Any] = {
            "sub": sub,
            "iss": emisor,
            "aud": audiencia,
            "iat": ahora,
            "exp": ahora + vence_en,
            **(claims_extra or {}),
        }
        if email is not None:
            claims["email"] = email
        return jwt.encode(
            claims, self._privada, algorithm="ES256", headers={"kid": kid or self.kid}
        )

    def token_hmac_con_la_publica(self, **kwargs: Any) -> str:
        """El ataque de confusion de algoritmo.

        La llave publica es PUBLICA. Si el verificador aceptara el `alg` que viene en la cabecera
        del propio token, un atacante la usaria como secreto de HMAC y su firma verificaria
        perfecto. Por eso `principal_del_token` pasa `algorithms=["ES256"]` explicito, y por eso
        esto tiene que dar rechazo.
        """
        ahora = int(time.time())
        claims = {
            "sub": "atacante",
            "iss": EMISOR,
            "aud": "authenticated",
            "iat": ahora,
            "exp": ahora + 3600,
            "email": CORREO,
            **kwargs,
        }
        secreto = str(self.jwk_publica["x"])
        return jwt.encode(claims, secreto, algorithm="HS256", headers={"kid": self.kid})


class CacheDeLlavesFalsa(CacheDeLlaves):
    """La cache, pero sirviendo un JWKS de memoria en vez de ir a la red.

    Hereda de la de verdad para que la logica que se prueba —vencimiento, candado, re-bajada por
    `kid` desconocido— sea LA MISMA que corre en produccion. Solo se reemplaza el viaje HTTP.
    """

    def __init__(self, jwks: dict[str, Any]) -> None:
        super().__init__("https://no-se-usa.invalid/jwks.json")
        self.jwks = jwks
        self.bajadas = 0

    async def _bajar(self) -> None:
        import time as _time

        self.bajadas += 1
        vence_en = _time.monotonic() + 3600.0
        from declaras.api.auth.jwks import _Entrada

        self._llaves = {
            str(k["kid"]): _Entrada(llave=k, vence_en=vence_en)
            for k in self.jwks.get("keys", [])
            if k.get("kid")
        }
