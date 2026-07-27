"""Esquema de base de datos. Solo estado operativo: nunca credenciales."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    challenge: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    progress: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ClientRow(Base):
    """El contribuyente. Persiste entre anios gravables."""

    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id_kind: Mapped[str] = mapped_column(String(8))
    id_number: Mapped[str] = mapped_column(String(20), index=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("id_kind", "id_number", name="uq_client_document"),)


class CaseRow(Base):
    """Un expediente: el trabajo de un cliente para un anio gravable."""

    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(36), ForeignKey("clients.id"), index=True)
    tax_year: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("client_id", "tax_year", name="uq_case_client_year"),)


class CaseDocumentRow(Base):
    """Un documento dentro de un expediente. `reading_json` guarda la lectura completa
    (campos, filas y avisos) serializada, tal como la produce el servicio de lectura."""

    __tablename__ = "case_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16))
    storage_uri: Mapped[str] = mapped_column(String(500))
    filename: Mapped[str] = mapped_column(String(200))
    content_sha256: Mapped[str] = mapped_column(String(64))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    extraction_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reading_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Cuando una consulta mas reciente trae el mismo documento, el anterior no se borra:
    # se marca reemplazado. La copia vieja sigue existiendo para la auditoria (la DIAN
    # puede preguntar hasta tres anios despues), pero deja de contar como vigente.
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CaseFlagRow(Base):
    """Algo que un contador debe revisar antes de dar el expediente por bueno."""

    __tablename__ = "case_flags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(String(500))
    severity: Mapped[str] = mapped_column(String(16))
    source_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class CaseEventRow(Base):
    """Bitacora de auditoria: registro append-only de todo lo que le paso al expediente."""

    __tablename__ = "case_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(String(500))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CasePartidaRow(Base):
    """Una partida del conciliador, con su resolucion, dentro de un expediente.

    `partida_json` guarda la partida COMPLETA (`Partida.model_dump()`), no un resumen: las
    cuatro marcas estructurales del cruce (`reportado_a`, `versiones_documento`,
    `version_que_rige`, `documentos_por_cruzar`) son lo que distingue "esta plata es de
    otra persona" de una nota de texto que la siguiente capa reescribe. Guardar menos
    dejaria esa marca fuera y el ingreso de un tercero entraria al caso del contribuyente.

    OJO CON EL ORDEN DE LAS CLAVES: JSONB de Postgres no lo preserva (reordena por longitud
    y luego bytes, medido contra Postgres 17.10). La semantica del conciliador no se apoya
    en el orden justamente por eso, y nadie debe leer "la ultima clave de
    `versiones_documento` es la mas nueva": cual rige lo dice `version_que_rige`.

    `sin_partida` marca las HUERFANAS: resoluciones cuya partida ya no existe en la
    re-derivacion del cruce (la DIAN republico el reporte sin esa fila). Se conservan
    porque botarlas esconde una decision de una persona y la deduccion que la sostenia; se
    vuelven a pasar a `refrescar` en cada reconstruccion, asi que si el id reaparece con
    las mismas cifras la decision se recupera sola.
    """

    __tablename__ = "case_partidas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    # El id del conciliador (`nit:CONCEPTO` y sus variantes). No es la llave primaria: es
    # texto libre derivado del reporte y puede pasar de 36 caracteres.
    partida_id: Mapped[str] = mapped_column(String(300))
    estado: Mapped[str] = mapped_column(String(24), index=True)
    sin_partida: Mapped[bool] = mapped_column(Boolean, default=False)
    partida_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("case_id", "partida_id", name="uq_partida_caso"),
    )


class CaseRespuestaRow(Base):
    """Lo que el cliente contesto a una pregunta, o la peticion que el contador cerro.

    Una sola tabla para las dos cosas a proposito: apagar una peticion es lo mismo en los
    dos caminos (`tiene=False` sobre la clave de la peticion), y dos mecanismos separados
    acabarian discrepando sobre cual peticion sigue viva.
    """

    __tablename__ = "case_respuestas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    pregunta: Mapped[str] = mapped_column(String(300))
    tiene: Mapped[bool] = mapped_column(Boolean)
    respuesta_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("case_id", "pregunta", name="uq_respuesta_caso_pregunta"),
    )


class CaseLiquidacionRow(Base):
    """Una version de la liquidacion, con el momento en que se calculo.

    Se guarda la liquidacion COMPLETA y no se recalcula al leerla: el preliminar es la foto
    de lo que se sabia antes de que llegara un solo documento del cliente, y recalcularlo
    con los datos de hoy borraria la ganancia que el producto existe para mostrar.
    """

    __tablename__ = "case_liquidaciones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    momento: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    impuesto: Mapped[int] = mapped_column(Integer)
    saldo: Mapped[int] = mapped_column(Integer)
    liquidacion_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    __table_args__ = (
        UniqueConstraint("case_id", "version", name="uq_liquidacion_caso_version"),
    )


class LoginAttemptRow(Base):
    """Contador de intentos fallidos por sujeto, para no bloquear cuentas."""

    __tablename__ = "login_attempts"

    subject_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
