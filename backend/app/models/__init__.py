"""SQLAlchemy models for OFC local store."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class LlmProfile(Base):
    """Named LLM endpoint + credentials (air-gapped friendly)."""

    __tablename__ = "llm_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    base_url: Mapped[str] = mapped_column(String(512))
    api_key_enc: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(256))
    temperature: Mapped[float] = mapped_column(default=0.3)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DbConnection(Base):
    """Read-only (by convention) database connection for context / examples."""

    __tablename__ = "db_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    dialect: Mapped[str] = mapped_column(String(32))  # sqlite|postgresql|mysql
    # Encrypted DSN or JSON connection params
    dsn_enc: Mapped[str] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    queries: Mapped[list["SavedQuery"]] = relationship(back_populates="connection")


class SavedQuery(Base):
    """Named SQL used to pull results or example reports."""

    __tablename__ = "saved_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("db_connections.id"))
    name: Mapped[str] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(32))  # results|examples|both
    sql_text: Mapped[str] = mapped_column(Text)
    # Optional column that holds report body when purpose includes examples
    example_body_column: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    connection: Mapped["DbConnection"] = relationship(back_populates="queries")


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    original_name: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(1024))
    mime_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(32), default="context")  # context|example|both
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Document(Base):
    """Finished / imported documents usable as examples or archive."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    filename: Mapped[str] = mapped_column(String(512), default="")
    format: Mapped[str] = mapped_column(String(32), default="markdown")  # markdown|docx|text
    body_md: Mapped[str] = mapped_column(Text, default="")
    source_report_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("report_projects.id"), nullable=True
    )
    # context | example | both — same roles as uploads
    role: Mapped[str] = mapped_column(String(32), default="example")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReportProject(Base):
    """A report job: brief + selected sources + drafts."""

    __tablename__ = "report_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    brief: Mapped[str] = mapped_column(Text, default="")
    # Context / results selections
    file_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    query_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    # Example selections (ignored when use_all_examples is true)
    document_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    example_file_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    use_all_examples: Mapped[bool] = mapped_column(default=True)
    llm_profile_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("llm_profiles.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="draft")
    style_notes_md: Mapped[str] = mapped_column(Text, default="")
    # Fingerprint of the examples blob used to produce style_notes_md
    style_notes_key: Mapped[str] = mapped_column(String(64), default="")
    outline_md: Mapped[str] = mapped_column(Text, default="")
    body_md: Mapped[str] = mapped_column(Text, default="")
    critique_md: Mapped[str] = mapped_column(Text, default="")
    # JSON: fonts, header/footer text, logo filenames under themes/{id}/
    theme_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    runs: Mapped[list["PipelineRun"]] = relationship(back_populates="project")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("report_projects.id"))
    stage: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="running")
    log_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped["ReportProject"] = relationship(back_populates="runs")
