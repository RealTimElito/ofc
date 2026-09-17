"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class LlmProfileIn(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    model: str
    temperature: float = 0.3
    max_tokens: int = 4096
    system_prompt: Optional[str] = None
    is_default: bool = False


class LlmProfileOut(BaseModel):
    id: int
    name: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    system_prompt: Optional[str]
    is_default: bool
    has_api_key: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class DbConnectionIn(BaseModel):
    name: str
    dialect: str = Field(description="sqlite | postgresql | mysql")
    dsn: str = Field(description="File path or SQLAlchemy DSN")
    notes: Optional[str] = None


class DbConnectionOut(BaseModel):
    id: int
    name: str
    dialect: str
    notes: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class SavedQueryIn(BaseModel):
    connection_id: int
    name: str
    purpose: str = Field(description="results | examples | both")
    sql_text: str
    example_body_column: Optional[str] = None


class SavedQueryOut(BaseModel):
    id: int
    connection_id: int
    name: str
    purpose: str
    sql_text: str
    example_body_column: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class QueryPreviewOut(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool


class FileOut(BaseModel):
    id: int
    original_name: str
    mime_type: Optional[str]
    size_bytes: int
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentIn(BaseModel):
    title: str
    body_md: str = ""
    filename: str = ""
    format: str = "markdown"
    role: str = "example"


class DocumentOut(BaseModel):
    id: int
    title: str
    filename: str
    format: str
    body_md: str
    source_report_id: Optional[int]
    role: str
    has_theme_docx: bool = False
    is_stub: bool = False
    stub_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PruneStubsIn(BaseModel):
    """Delete library stubs. Omit ids (or pass null) to remove all detected stubs."""

    ids: Optional[list[int]] = None


class PruneStubsOut(BaseModel):
    deleted_ids: list[int]
    deleted_count: int


class ReportProjectIn(BaseModel):
    title: str
    brief: str = ""
    file_ids: list[int] = Field(default_factory=list)
    query_ids: list[int] = Field(default_factory=list)
    document_ids: list[int] = Field(default_factory=list)
    example_file_ids: list[int] = Field(default_factory=list)
    use_all_examples: bool = True
    llm_profile_id: Optional[int] = None


class ReportTheme(BaseModel):
    heading_font: Optional[str] = None
    body_font: Optional[str] = None
    header_text: str = ""
    footer_text: str = ""
    header_logo: Optional[str] = None
    footer_logo: Optional[str] = None
    source_label: str = ""


class ThemeImportIn(BaseModel):
    source: str = Field(description="file | document")
    source_id: int


class ReportProjectUpdate(BaseModel):
    title: Optional[str] = None
    brief: Optional[str] = None
    file_ids: Optional[list[int]] = None
    query_ids: Optional[list[int]] = None
    document_ids: Optional[list[int]] = None
    example_file_ids: Optional[list[int]] = None
    use_all_examples: Optional[bool] = None
    llm_profile_id: Optional[int] = None
    body_md: Optional[str] = None
    outline_md: Optional[str] = None
    theme: Optional[ReportTheme] = None


class ReportProjectOut(BaseModel):
    id: int
    title: str
    brief: str
    file_ids: list[int]
    query_ids: list[int]
    document_ids: list[int]
    example_file_ids: list[int]
    use_all_examples: bool
    llm_profile_id: Optional[int]
    status: str
    style_notes_md: str = ""
    style_notes_key: str = ""
    outline_md: str
    body_md: str
    critique_md: str
    theme: ReportTheme = Field(default_factory=ReportTheme)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PipelineRunOut(BaseModel):
    id: int
    project_id: int
    stage: str
    status: str
    log_text: str
    created_at: datetime

    model_config = {"from_attributes": True}


class GenerateIn(BaseModel):
    stage: str = Field(
        default="full",
        description="style_notes | outline | draft | critique | revise | full",
    )
    with_critique: bool = True


class MarkDoneIn(BaseModel):
    role: str = Field(
        default="example",
        description="How to store in the document library: context|example|both",
    )


class SettingsOut(BaseModel):
    llm_base_url: str
    llm_model: str
    data_dir: str
    air_gapped: bool = True


class HealthOut(BaseModel):
    status: str
    llm_configured: bool
