from dataclasses import dataclass, field
from typing import Any, Literal

HealthStatus = Literal['healthy','degraded','unavailable']

@dataclass(frozen=True)
class GatewayCapabilities:
    connection_test: bool=True; schema: bool=True; preview: bool=True; query: bool=True; cancel: bool=False; health: bool=True; readonly: bool=True; cost_estimate: bool=False; relationships: bool=False

@dataclass(frozen=True)
class GatewayDescriptor:
    id: str; name: str; backend: str; dialect: str; readonly: bool=True; metadata: dict[str,Any]=field(default_factory=dict)
    def __post_init__(self):
        if not all(str(v).strip() for v in (self.id,self.name,self.backend,self.dialect)): raise ValueError('gateway fields required')
        if not self.readonly: raise ValueError('gateways must be readonly')
        if any(k.lower() in {'password','secret','token','dsn','credential','credentials','api_key'} for k in self.metadata): raise ValueError('credentials')

@dataclass(frozen=True)
class GatewayQuery:
    sql: str; params: dict[str,Any]=field(default_factory=dict); max_rows: int=1000; offset: int=0; timeout_seconds: float|None=None; operation_id: str=''
    def __post_init__(self):
        if not self.sql.strip(): raise ValueError('query SQL is required')
        if self.max_rows < 1 or self.offset < 0: raise ValueError('query bounds are invalid')

@dataclass(frozen=True)
class GatewayResult:
    columns: list[str]; rows: list[list[Any]]; row_count: int; truncated: bool; operation_id: str=''; metadata: dict[str,Any]=field(default_factory=dict)
    def __post_init__(self):
        if self.row_count != len(self.rows) or any(len(r)!=len(self.columns) for r in self.rows): raise ValueError('result row width mismatch')

@dataclass(frozen=True)
class GatewayCostEstimate:
    backend: str
    supported: bool
    estimated_rows: int | None = None
    estimated_cost: float | None = None
    full_scan: bool | None = None
    index_used: bool | None = None
    reason: str = ""
    plan: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.estimated_rows is not None and self.estimated_rows < 0:
            raise ValueError("estimated_rows must be non-negative")
        if self.estimated_cost is not None and self.estimated_cost < 0:
            raise ValueError("estimated_cost must be non-negative")

@dataclass(frozen=True)
class GatewayRelationship:
    from_table: str
    from_columns: list[str]
    to_table: str
    to_columns: list[str]
    cardinality: str = "unknown"
    confidence: float = 1.0
    source: str = "database_metadata"

@dataclass(frozen=True)
class GatewaySchemaDetails:
    tables: dict[str, dict[str, Any]]
    relationships: list[GatewayRelationship] = field(default_factory=list)

@dataclass(frozen=True)
class GatewayHealth:
    status: HealthStatus; message: str; latency_ms: int=0; details: dict[str,Any]=field(default_factory=dict)
    def __post_init__(self):
        if self.status not in {'healthy','degraded','unavailable'} or self.latency_ms < 0: raise ValueError('invalid health')

class GatewayError(RuntimeError):
    def __init__(self, code: str, message: str, *, operation: str='', retryable: bool=False): self.code=code; self.operation=operation; self.retryable=retryable; super().__init__(message)
    def as_dict(self): return {'code':self.code,'message':str(self),'operation':self.operation,'retryable':self.retryable}
