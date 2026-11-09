"""Query request contracts shared by query and export endpoints."""
from pydantic import BaseModel, Field, model_validator
from app.agent.contracts import AnalysisMode


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    client_request_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    task_id: str | None = Field(default=None, max_length=36)
    follow_up: bool = False
    sql: str | None = None
    datasource: str | None = None
    params: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    max_rows: int | None = Field(default=None, ge=1, le=10000)
    offset: int = Field(default=0, ge=0, le=1000000)
    require_approval: bool = False
    analysis_mode: AnalysisMode | None = None
    branch_from_task_id: str | None = Field(default=None, max_length=36)
    branch_from_trace_id: str | None = Field(default=None, max_length=128)
    branch_label: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def validate_branch_request(self):
        branch_values = (self.branch_from_task_id, self.branch_from_trace_id)
        if any(branch_values) and not all(branch_values):
            raise ValueError("E_BRANCH_ORIGIN_REQUIRED:创建分析分支必须同时指定来源任务和轮次")
        if all(branch_values) and (self.task_id or self.follow_up):
            raise ValueError("E_BRANCH_REQUEST_CONFLICT:分析分支不能与 task_id/follow_up 同时使用")
        return self
