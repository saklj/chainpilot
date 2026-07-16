"""Streaming material-diagnosis endpoint."""

import json
from typing import Annotated

import duckdb
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from agent.diagnose import diagnose_material_events, get_risk_detail
from agent.llm import DeepSeekClient
from app.deps import get_db, get_llm
from app.schemas import DiagnosisRequest

router = APIRouter(prefix="/api/diagnose", tags=["diagnose"])
Db = Annotated[duckdb.DuckDBPyConnection, Depends(get_db)]
Llm = Annotated[DeepSeekClient, Depends(get_llm)]


@router.post("/stream")
def diagnose_stream(request: DiagnosisRequest, connection: Db, llm: Llm) -> StreamingResponse:
    try:
        get_risk_detail(connection, request.material_pn)
    except ValueError as error:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "material_not_found",
                "message": f"Material {request.material_pn} not found",
            },
        ) from error

    def event_source():
        try:
            for event in diagnose_material_events(llm, connection, request.material_pn):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as error:
            event = {"type": "error", "message": str(error)}
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")
