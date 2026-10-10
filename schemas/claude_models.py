"""Data models cho kết nối Claude Desktop (MCP): token cá nhân + gói bàn giao
(handoff) từ quote trong báo cáo."""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

TaskType = Literal["pdf", "excel", "strategy", "other"]


class ApiTokenCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)  # vd "MacBook công ty"


class ApiTokenSummary(BaseModel):
    id: int
    name: str
    token_prefix: str
    created_at: datetime
    expires_at: datetime
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class ApiTokenCreated(ApiTokenSummary):
    token: str  # token gốc — chỉ trả về đúng 1 lần lúc tạo


class HandoffCreateRequest(BaseModel):
    # Rỗng = không bôi đen đoạn nào (hỏi tự do về báo cáo).
    quote: str = Field("", max_length=4000)
    question: str = Field(..., min_length=1, max_length=2000)
    task_type: TaskType = "other"


class HandoffCreated(BaseModel):
    handoff_id: str
    prompt: str  # câu ngắn để copy vào Claude Desktop
    expires_at: datetime


class HandoffPayload(BaseModel):
    handoff_id: str
    report_date: str
    task_type: TaskType
    quote: str
    question: str
    instructions: str  # văn bản đã dựng sẵn cho Claude (xem services/claude_connect.py)


class GatewayToolDef(BaseModel):
    name: str
    description: str
    input_schema: Dict[str, Any]


class GatewayToolCall(BaseModel):
    # None = báo cáo published mới nhất. Client MCP truyền ngày của handoff đang làm.
    # Định dạng YYYY-MM-DD được validate ở router (lỗi trả `detail` dạng chuỗi dễ đọc).
    report_date: Optional[str] = None
    input: Dict[str, Any] = Field(default_factory=dict)


class GatewayToolResult(BaseModel):
    result: str
    # Ngày báo cáo ngữ cảnh đã dùng — MCP client báo lại cho Claude để không nhầm ngày.
    report_date: Optional[str] = None


class GatewayInstructions(BaseModel):
    instructions: str


class GatewayPromptArgument(BaseModel):
    name: str
    description: str
    required: bool = False


class GatewayPromptDef(BaseModel):
    name: str
    title: str
    description: str
    arguments: List[GatewayPromptArgument] = Field(default_factory=list)


class GatewayPromptRequest(BaseModel):
    arguments: Dict[str, str] = Field(default_factory=dict)


class GatewayPromptResult(BaseModel):
    description: str
    text: str


class GatewayMe(BaseModel):
    email: str
    token_name: str
    tools: List[str]
