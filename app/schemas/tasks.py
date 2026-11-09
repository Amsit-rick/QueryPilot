"""Data task HTTP contracts."""
from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    datasource: str = Field(default="")
    title: str = Field(default="", max_length=255)


class TaskStatusRequest(BaseModel):
    status: str = Field(min_length=1, max_length=32)
