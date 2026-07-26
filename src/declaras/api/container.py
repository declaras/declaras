"""Composicion de dependencias: el unico lugar donde se ensambla la aplicacion.

Los servicios reciben puertos por constructor, asi que cambiar Playwright por el doble
de prueba, o disco local por GCS, es cambiar una linea aca y nada mas.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from declaras.adapters.dian.factory import build_dian_connector
from declaras.adapters.persistence.engine import (
    create_engine,
    create_schema,
    create_session_factory,
)
from declaras.adapters.persistence.job_repository import SqlJobRepository
from declaras.adapters.persistence.login_guard import SqlLoginAttemptGuard
from declaras.adapters.storage.factory import build_document_store
from declaras.config import Settings
from declaras.domain.ports import DianConnector, DocumentStore, JobRepository, LoginAttemptGuard
from declaras.observability import get_logger
from declaras.services.credential_vault import InMemoryCredentialVault
from declaras.services.extraction import ExtractionService
from declaras.services.job_runner import JobRunner
from declaras.services.notifier import WebhookNotifier
from declaras.services.session_registry import InMemorySessionRegistry

log = get_logger(__name__)


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    jobs: JobRepository
    guard: LoginAttemptGuard
    store: DocumentStore
    connector: DianConnector
    vault: InMemoryCredentialVault
    registry: InMemorySessionRegistry
    extraction: ExtractionService
    runner: JobRunner

    @classmethod
    def build(cls, settings: Settings) -> Container:
        engine = create_engine(settings.database_url)
        sessions = create_session_factory(engine)

        jobs = SqlJobRepository(sessions)
        guard = SqlLoginAttemptGuard(sessions, max_attempts=settings.dian_max_login_attempts)
        store = build_document_store(settings)
        connector = build_dian_connector(settings)
        vault = InMemoryCredentialVault()
        registry = InMemorySessionRegistry(ttl_seconds=settings.worker_lease_ttl_s)

        extraction = ExtractionService(
            connector=connector,
            store=store,
            jobs=jobs,
            guard=guard,
            vault=vault,
            registry=registry,
            notifier=WebhookNotifier(),
            settings=settings,
        )
        runner = JobRunner(
            jobs=jobs,
            extraction=extraction,
            vault=vault,
            registry=registry,
            settings=settings,
        )
        return cls(
            settings=settings,
            engine=engine,
            jobs=jobs,
            guard=guard,
            store=store,
            connector=connector,
            vault=vault,
            registry=registry,
            extraction=extraction,
            runner=runner,
        )

    async def startup(self) -> None:
        await create_schema(self.engine)
        if self.settings.worker_enabled:
            await self.runner.start()
        log.info(
            "container.started",
            env=self.settings.env.value,
            dian_adapter=self.settings.dian_adapter.value,
            storage=self.settings.storage_backend.value,
        )

    async def shutdown(self) -> None:
        await self.runner.stop()
        shutdown = getattr(self.connector, "shutdown", None)
        if callable(shutdown):
            await shutdown()
        await self.engine.dispose()
        log.info("container.stopped")
