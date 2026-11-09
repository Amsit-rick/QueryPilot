from pydantic import BaseModel, ConfigDict, Field


class ProviderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    adapter: str = "direct"
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=160)
    api_key_env: str = Field(min_length=2, max_length=128)
    enabled: bool = True
    is_default: bool = False


class ProviderUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    adapter: str | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    api_key_env: str | None = Field(default=None, min_length=2, max_length=128)
    enabled: bool | None = None
    is_default: bool | None = None


class PromptCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="sql_generation", min_length=1, max_length=64)
    system_prompt: str = Field(min_length=1, max_length=8000)
    activate: bool = False
