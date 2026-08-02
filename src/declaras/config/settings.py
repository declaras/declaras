"""Configuracion por entorno. Todo se inyecta por variables DECLARAS_*."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class StorageBackend(StrEnum):
    LOCAL = "local"
    GCS = "gcs"


class DianAdapterKind(StrEnum):
    FAKE = "fake"
    HTTP = "http"
    PLAYWRIGHT = "playwright"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DECLARAS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Environment = Environment.LOCAL
    log_level: str = "INFO"

    # ── Origenes que pueden llamar a la API desde un navegador ──────────────────────────────
    #
    # Hace falta desde que el front le habla DIRECTO al backend: antes pasaba por un proxy del
    # mismo dominio y el navegador no preguntaba nada. Ahora son dominios distintos, asi que el
    # navegador pide permiso primero y sin esta lista lo niega.
    #
    # NoDecode: texto separado por comas, no JSON.
    #
    # Vacia => ningun navegador puede llamar. No se pone `*` como default ni "para desarrollo": con
    # credenciales habilitadas el comodin permitiria que CUALQUIER pagina que alguien visite haga
    # peticiones a esta API con la sesion del contador. Los origenes se enumeran.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # ── Auth de personas (Supabase Auth) ────────────────────────────────────────────────────
    #
    # `supabase_url` es el proyecto contra el que se valida el token: de ahi salen el emisor
    # esperado y la URL de las llaves publicas. Sin el no hay auth de usuario y solo sirve la
    # llave de API — que es el estado de hoy y por eso el default es None.
    supabase_url: str | None = None

    # QUIEN PUEDE ENTRAR, que no es lo mismo que quien tiene un token valido.
    #
    # Un JWT de Supabase prueba que alguien tiene una cuenta EN ESE PROYECTO, no que pueda ver
    # declaraciones de renta. Con el registro publico encendido, cualquiera se crea una cuenta y
    # su token pasa la validacion sin una sola falla — la firma es legitima.
    #
    # Por eso van las dos cosas: el registro apagado en el dashboard Y esta lista. Son
    # redundantes a proposito: la primera vive en una consola web que nadie versiona y que se
    # puede cambiar por accidente; esta esta en la configuracion del despliegue.
    #
    # Vacia => nadie entra. Una lista de permitidos que al quedar vacia permite a todos es la
    # forma mas comun de que esto se vuelva decorativo.
    contadores: Annotated[list[str], NoDecode] = Field(default_factory=list)

    database_url: str = "sqlite+aiosqlite:///./var/declaras.db"

    storage_backend: StorageBackend = StorageBackend.LOCAL
    storage_local_root: Path = Path("./var/documents")
    storage_gcs_bucket: str | None = None

    dian_base_url: str = "https://muisca.dian.gov.co"
    dian_adapter: DianAdapterKind = DianAdapterKind.HTTP
    dian_headless: bool = True
    dian_max_concurrent_sessions: int = Field(default=2, ge=1, le=10)
    dian_nav_timeout_ms: int = Field(default=45_000, ge=5_000)
    dian_max_login_attempts: int = Field(default=2, ge=1, le=2)
    dian_capture_evidence: bool = True
    dian_challenge_ttl_s: int = Field(default=600, ge=60)

    worker_enabled: bool = True
    worker_poll_interval_s: float = Field(default=2.0, gt=0)
    worker_lease_ttl_s: int = Field(default=900, ge=60)
    worker_max_attempts: int = Field(default=3, ge=1)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]
        return value

    @field_validator("contadores", mode="before")
    @classmethod
    def _parse_contadores(cls, value: object) -> object:
        # En minusculas porque los correos no distinguen mayusculas para esto y la comparacion
        # tiene que ser la misma de los dos lados: una lista con "Esteban@..." y un token con
        # "esteban@..." dejarian a la persona afuera sin decir por que.
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip().lower() for item in value if str(item).strip()]
        return value

    @property
    def auth_de_usuario_activo(self) -> bool:
        """Si se puede validar un token de persona.

        Las dos condiciones van juntas y no por separado: con proyecto pero sin lista, todo token
        valido entraria; con lista pero sin proyecto, no hay nada contra lo que validar.
        """
        return bool(self.supabase_url and self.contadores)

    @property
    def is_production(self) -> bool:
        return self.env is Environment.PRODUCTION


@lru_cache
def get_settings() -> Settings:
    return Settings()
