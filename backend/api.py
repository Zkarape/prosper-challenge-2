"""HTTP interface for the text scheduling workbench.

Run from the repository root with ``make api``.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Literal

from agent_builder import AgentConfigRepository
from evaluation import EvaluationRunner
from scaling import ScalabilityRunner
from scheduling import shared_conversation_service


load_dotenv(Path(__file__).parent / ".env", override=False)


class TurnRequest(BaseModel):
    utterance: str = Field(min_length=1, max_length=2000)
    message_id: str | None = Field(default=None, min_length=1, max_length=120)


class EndConversationRequest(BaseModel):
    outcome: Literal["AUTO", "PATIENT_ABANDONED", "SYSTEM_ERROR"] = "AUTO"


class EvaluationRunRequest(BaseModel):
    case_ids: list[str] | None = Field(default=None, max_length=40)
    extractor: Literal["configured", "local"] = "configured"


class AgentConfigRequest(BaseModel):
    config: dict


class ScalabilityRunRequest(BaseModel):
    target_sessions: int = Field(default=100, ge=1, le=100)


app = FastAPI(title="Prosper Scheduling API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
    ],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

service = shared_conversation_service()
EVALUATION_DATASET_PATH = (
    Path(__file__).parent / "tests" / "fixtures" / "context_management_eval.json"
)
evaluation_runner = EvaluationRunner(
    catalog=service.catalog,
    configured_extractor=service.extractor,
    dataset_path=EVALUATION_DATASET_PATH,
)
agent_repository = AgentConfigRepository(Path(__file__).parent / "example_flow.json")
scalability_runner = ScalabilityRunner(catalog=service.catalog)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "extractor_mode": service.extractor_mode,
        **service.store.health(),
    }


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
        "status": conversation.status,
        "outcome": conversation.outcome,
        "safe": conversation.safe,
        "started_at": conversation.started_at,
        "ended_at": conversation.ended_at,
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
        if str(exc) in {"CONVERSATION_BUSY", "CONVERSATION_CLOSED"}:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/conversations/{conversation_id}/end")
def end_conversation(
    conversation_id: str, request: EndConversationRequest
) -> dict:
    try:
        return service.finish_conversation(
            conversation_id,
            forced_outcome=request.outcome if request.outcome != "AUTO" else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


@app.get("/api/conversations/{conversation_id}/evaluation")
def conversation_evaluation(conversation_id: str) -> dict:
    try:
        return service.conversation_evaluation(conversation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


@app.get("/api/evaluations/summary")
def evaluation_summary() -> dict:
    return service.evaluation_summary()


@app.get("/api/evaluations/dataset")
def evaluation_dataset() -> dict:
    dataset = evaluation_runner.dataset()
    return {
        "dataset_version": dataset.get("dataset_version"),
        "catalog_version": dataset.get("catalog_version"),
        "description": dataset.get("description"),
        "case_count": dataset["case_count"],
        "defined_case_count": dataset["defined_case_count"],
        "manual_authored_case_count": dataset["manual_authored_case_count"],
        "cases": dataset["cases"],
    }


@app.post("/api/evaluations/runs", status_code=202)
def run_evaluation(request: EvaluationRunRequest) -> dict:
    try:
        return evaluation_runner.start(
            case_ids=request.case_ids,
            extractor=request.extractor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/evaluations/runs/latest")
def latest_evaluation_run() -> dict:
    run = evaluation_runner.latest()
    if run is None:
        raise HTTPException(status_code=404, detail="NO_EVALUATION_RUN")
    return run


@app.get("/api/evaluations/runs/{run_id}")
def get_evaluation_run(run_id: str) -> dict:
    run = evaluation_runner.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="EVALUATION_RUN_NOT_FOUND")
    return run


@app.post("/api/scalability/runs", status_code=202)
def run_scalability_test(request: ScalabilityRunRequest) -> dict:
    try:
        return scalability_runner.start(target_sessions=request.target_sessions)
    except ValueError as exc:
        status_code = 409 if str(exc) == "SCALABILITY_TEST_ALREADY_RUNNING" else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@app.get("/api/scalability/runs/latest")
def latest_scalability_test() -> dict:
    run = scalability_runner.latest()
    if run is None:
        raise HTTPException(status_code=404, detail="NO_SCALABILITY_RUN")
    return run


@app.get("/api/scalability/runs/{run_id}")
def get_scalability_test(run_id: str) -> dict:
    run = scalability_runner.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="SCALABILITY_RUN_NOT_FOUND")
    return run


@app.get("/api/agent")
def get_agent_config() -> dict:
    try:
        config = agent_repository.load()
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=500, detail=f"AGENT_CONFIG_INVALID: {exc}") from exc
    return {
        "config": config.to_dict(),
        "validation": agent_repository.report(config),
    }


@app.put("/api/agent")
def save_agent_config(request: AgentConfigRequest) -> dict:
    try:
        config = agent_repository.save(request.config)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "config": config.to_dict(),
        "validation": agent_repository.report(config),
        "saved": True,
    }


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str) -> None:
    service.delete_conversation(conversation_id)
