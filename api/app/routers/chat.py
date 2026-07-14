"""Natural-language analytics endpoint."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from agent.chat import answer_question
from agent.llm import DeepSeekClient
from app.deps import get_llm
from app.schemas import ChatRequest, ChatResult

router = APIRouter(prefix="/api/chat", tags=["chat"])
Llm = Annotated[DeepSeekClient, Depends(get_llm)]


@router.post("", response_model=ChatResult)
def chat(request: ChatRequest, llm: Llm) -> dict[str, Any]:
    payload = answer_question(request.question, llm).to_dict()
    verdict = payload["verdict"]
    if verdict is not None:
        payload["verdict"] = {
            "verdict": verdict["verdict"],
            "matched": [
                {"value": value, "row": coordinate[0], "column": coordinate[1]}
                for value, coordinate in verdict["matched"].items()
            ],
            "unmatched": verdict["unmatched"],
            "checked_count": verdict["checked_count"],
        }
    return payload
