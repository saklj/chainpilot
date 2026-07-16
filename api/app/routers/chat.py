"""Natural-language analytics endpoints."""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from agent.chat import answer_question, answer_question_events, verdict_to_dict
from agent.llm import DeepSeekClient
from app.deps import FewShotsProvider, get_few_shots_provider, get_llm
from app.schemas import ChatRequest, ChatResult

router = APIRouter(prefix="/api/chat", tags=["chat"])
Llm = Annotated[DeepSeekClient, Depends(get_llm)]
Provider = Annotated[FewShotsProvider, Depends(get_few_shots_provider)]
LOGGER = logging.getLogger(__name__)


def _few_shots(provider: FewShotsProvider, question: str):
    try:
        return provider(question)
    except Exception as error:
        LOGGER.warning("Injected RAG provider failed; using fixed examples: %s", error)
        return None


@router.post("", response_model=ChatResult)
def chat(request: ChatRequest, llm: Llm, provider: Provider) -> dict[str, Any]:
    response = answer_question(
        request.question, llm, few_shots=_few_shots(provider, request.question)
    )
    payload = response.to_dict()
    payload["verdict"] = verdict_to_dict(response.verdict)
    return payload


@router.post("/stream")
def chat_stream(request: ChatRequest, llm: Llm, provider: Provider) -> StreamingResponse:
    few_shots = _few_shots(provider, request.question)

    def event_source():
        for event in answer_question_events(request.question, llm, few_shots=few_shots):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")
