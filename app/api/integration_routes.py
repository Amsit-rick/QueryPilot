from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import require_query_access
from app.schemas.integration import ReadOnlyToolCallRequest
from app.services.read_only_integration_service import (
    ReadOnlyIntegrationError,
    get_read_only_integration_service,
)

router = APIRouter(prefix="/api/v1/integrations/read-only", tags=["integrations"])


@router.get("/tools")
def list_tools(identity: dict = Depends(require_query_access)):
    del identity
    return {"version": 1, "read_only": True, "tools": get_read_only_integration_service().list_tools()}


@router.post("/call")
def call_tool(request: ReadOnlyToolCallRequest, identity: dict = Depends(require_query_access)):
    try:
        return get_read_only_integration_service().invoke(identity, request.tool, request.arguments)
    except ReadOnlyIntegrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from exc
