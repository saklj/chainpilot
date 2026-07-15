"""Natural-language analytics endpoints."""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from agent.chat import answer_question, answer_question_events, verdict_to_dict
from agent.llm import DeepSeekClient
from app.deps import get_llm
from app.schemas import ChatRequest, ChatResult

router = APIRouter(prefix="/api/chat", tags=["chat"])
Llm = Annotated[DeepSeekClient, Depends(get_llm)]


@router.post("", response_model=ChatResult)
def chat(request: ChatRequest, llm: Llm) -> dict[str, Any]:
    response = answer_question(request.question, llm)
    payload = response.to_dict()
    payload["verdict"] = verdict_to_dict(response.verdict)
    return payload


@router.post("/stream")
def chat_stream(request: ChatRequest, llm: Llm) -> StreamingResponse:
    def event_source():
        for event in answer_question_events(request.question, llm):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")
