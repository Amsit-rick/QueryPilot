from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["accepted", "corrected", "rejected", "failed"]
    note: str = Field(default="", max_length=500)
    corrected_sql: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def validate_correction(self):
        if self.outcome == "corrected" and not self.corrected_sql.strip():
            raise ValueError("修正反馈必须提供 corrected_sql")
        if self.outcome != "corrected" and self.corrected_sql.strip():
            raise ValueError("只有修正反馈可以提供 corrected_sql")
        return self


class TaskFeedbackReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    dataset_version: str = Field(default="", max_length=128, pattern=r"^[a-zA-Z0-9._-]*$")
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_dataset_version(self):
        if self.decision == "approved" and not self.dataset_version.strip():
            raise ValueError("批准反馈必须指定 dataset_version")
        return self
