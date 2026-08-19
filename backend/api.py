"""HTTP interface for the text scheduling workbench.

Run from the repository root with ``make api``.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from scheduling import ConversationService


load_dotenv(Path(__file__).parent / ".env", override=False)


class TurnRequest(BaseModel):
    utterance: str = Field(min_length=1, max_length=2000)
    message_id: str | None = Field(default=None, min_length=1, max_length=120)


app = FastAPI(title="Prosper Scheduling API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
    ],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

service = ConversationService.default()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "extractor_mode": service.extractor_mode}


@app.post("/api/conversations", status_code=201)
def create_conversation() -> dict:
    return service.create_conversation()


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> dict:
    try:
        conversation = service.get_conversation(conversation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    return {
        "conversation_id": conversation_id,
        "patient_request": conversation.patient_request.to_dict(),
        "state": conversation.patient_request.to_dict(),
        "message_number": conversation.message_number,
        "pending_offer": (
            conversation.pending_offer.to_dict(include_values=False)
            if conversation.pending_offer
            else None
        ),
        "booking": conversation.booking,
        "last_result": conversation.last_result,
    }


@app.post("/api/conversations/{conversation_id}/turns")
def process_turn(conversation_id: str, request: TurnRequest) -> dict:
    try:
        return service.process_turn(
            conversation_id, request.utterance, message_id=request.message_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str) -> None:
    service.conversations.pop(conversation_id, None)
