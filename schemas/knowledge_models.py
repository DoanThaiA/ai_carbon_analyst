"""Data models cho tài liệu tri thức của Jenny — xem db.models.KnowledgeDocument, services/knowledge.py và
api/routers/admin_knowledge.py."""
from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from db.models import KNOWLEDGE_SECTIONS

DocKind = Literal["regulation", "research", "market_report", "internal", "reference"]


class KnowledgePresignRequest(BaseModel):
    file_name: str = Field(..., min_length=1, max_length=255)
    content_type: str
    size: int = Field(..., gt=0)


class KnowledgePresignResponse(BaseModel):
    upload_url: str
    file_key: str


class _KnowledgeFields(BaseModel):
    """Trường admin sửa được (tạo mới + PATCH)."""
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    publisher: Optional[str] = Field(None, max_length=200)
    issued_on: Optional[date] = None
    doc_kind: Optional[DocKind] = None
    language: Optional[Literal["vi", "en"]] = None
    use_in_report: Optional[bool] = None
    # Rỗng / None = mọi mục báo cáo.
    report_sections: Optional[List[str]] = None
    use_in_chat: Optional[bool] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    verified: Optional[bool] = None
    note: Optional[str] = Field(None, max_length=2000)

    @field_validator("title", "publisher", "note")
    @classmethod
    def _strip(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() or None if isinstance(v, str) else v

    @field_validator("report_sections")
    @classmethod
    def _sections(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        bad = [s for s in v if s not in KNOWLEDGE_SECTIONS]
        if bad:
            raise ValueError(f"Mục báo cáo không hợp lệ: {', '.join(bad)}")
        return [s for s in KNOWLEDGE_SECTIONS if s in v] or None

    @model_validator(mode="after")
    def _validity(self):
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("Ngày hết hiệu lực phải sau ngày bắt đầu hiệu lực.")
        return self


class KnowledgeCreateRequest(_KnowledgeFields):
    file_key: str = Field(..., min_length=1, max_length=500)
    file_name: str = Field(..., min_length=1, max_length=255)
    title: str = Field(..., min_length=1, max_length=300)


class KnowledgeUpdateRequest(_KnowledgeFields):
    is_active: Optional[bool] = None


class KnowledgeDocumentOut(BaseModel):
    id: int
    title: str
    publisher: Optional[str] = None
    issued_on: Optional[date] = None
    doc_kind: str
    language: str
    file_name: str
    media_type: str
    size_bytes: int
    status: str
    error_message: Optional[str] = None
    warnings: Optional[List[str]] = None
    page_count: Optional[int] = None
    char_count: Optional[int] = None
    chunk_count: Optional[int] = None
    is_active: bool
    use_in_report: bool
    report_sections: Optional[List[str]] = None
    use_in_chat: bool
    valid_from: date
    valid_to: Optional[date] = None
    verified: bool
    note: Optional[str] = None
    uploaded_by: str
    created_at: datetime
    updated_at: datetime
    processed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class KnowledgeSection(BaseModel):
    heading: Optional[str] = None
    page_from: Optional[int] = None
    page_to: Optional[int] = None
    text: str


class KnowledgeChunkOut(BaseModel):
    chunk_id: int
    chunk_index: int
    content: str
    heading: Optional[str] = None
    page_from: Optional[int] = None
    page_to: Optional[int] = None


class KnowledgeDocumentDetail(KnowledgeDocumentOut):
    sections: List[KnowledgeSection] = []


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    purpose: Literal["report", "chat"] = "chat"
    section: Optional[str] = None
    doc_id: Optional[int] = None
    as_of: Optional[date] = None


class KnowledgeSearchHit(BaseModel):
    chunk_id: int
    doc_id: int
    title: str
    score: float
    content: str
    heading: Optional[str] = None
    page_from: Optional[int] = None
    page_to: Optional[int] = None


class KnowledgeDownloadUrl(BaseModel):
    url: str
