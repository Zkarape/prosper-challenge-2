"""Observable Pipecat voice adapter for the deterministic scheduler.

Runtime path:

    browser microphone -> WebRTC -> ElevenLabs final transcript
        -> structured LLM extraction -> deterministic scheduling engine
        -> checked response -> ElevenLabs TTS -> browser audio

Each processed voice turn is also sent to the embedded frontend as an RTVI
server message so extraction usage, state, rules, and the next action are
inspectable while the call is running.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.elevenlabs.stt import (
    CommitStrategy,
    ElevenLabsRealtimeSTTService,
)
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.turns.user_turn_processor import UserTurnProcessor
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.workers.runner import WorkerRunner

from observability import configure_logging, get_logger, transcript_fields
from scheduling import ConversationService, shared_conversation_service
from scheduling.service import GREETING
from agent_builder import AgentConfigRepository


load_dotenv(Path(__file__).parent / ".env", override=True)
configure_logging("voice")
logger = get_logger("voice")

DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
TURN_END_SILENCE_SECS = 0.45
TURN_END_FAILSAFE_SECS = 2.5

transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is required for the live voice pipeline. "
            "Add it to backend/.env and restart make run."
        )
    return value


class SchedulingTurnProcessor(FrameProcessor):
    """Run one semantically complete patient turn through the checked service."""

    def __init__(
        self,
        service: ConversationService,
        conversation_id: str,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.service = service
        self.conversation_id = conversation_id
        self.rtvi = None
        self._transcript_segments: list[str] = []
        self._first_transcript_at: float | None = None
        self._turn_queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        self._turn_worker_task: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            patient_text = frame.text.strip()
            if patient_text:
                if not self._transcript_segments:
                    self._first_transcript_at = time.perf_counter()
                self._transcript_segments.append(patient_text)
                logger.bind(
                    event="voice_transcript_segment",
                    conversation_id=self.conversation_id,
                    **transcript_fields(patient_text),
                ).debug("Buffered patient speech segment")
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
        if not isinstance(frame, UserStoppedSpeakingFrame):
            return

        patient_text = " ".join(self._transcript_segments).strip()
        self._transcript_segments.clear()
        turn_boundary_ms = (
            round((time.perf_counter() - self._first_transcript_at) * 1000)
            if self._first_transcript_at is not None
            else 0
        )
        self._first_transcript_at = None
        if not patient_text:
            return

        # Never hold Pipecat's frame processor open while the LLM and database
        # work runs. Audio, STT and VAD frames must keep flowing. A single
        # consumer still preserves conversation turn order.
        await self._turn_queue.put((patient_text, turn_boundary_ms))
        self._ensure_turn_worker()

    def _ensure_turn_worker(self) -> None:
        if self._turn_worker_task is not None and not self._turn_worker_task.done():
            return
        coroutine = self._run_turns()
        if getattr(self, "_task_manager", None) is not None:
            self._turn_worker_task = self.create_task(coroutine, "scheduling-turn-worker")
        else:
            # Unit tests can exercise the processor without a Pipecat StartFrame.
            self._turn_worker_task = asyncio.create_task(coroutine)

    async def _run_turns(self) -> None:
        while True:
            patient_text, turn_boundary_ms = await self._turn_queue.get()
            try:
                await self._process_complete_turn(patient_text, turn_boundary_ms)
            finally:
                self._turn_queue.task_done()

    async def wait_for_pending_turns(self) -> None:
        """Wait until every completed speech turn has been processed."""

        await self._turn_queue.join()

    async def cleanup(self):
        if self._turn_worker_task is not None:
            if getattr(self, "_task_manager", None) is not None:
                await self.cancel_task(self._turn_worker_task)
            else:
                self._turn_worker_task.cancel()
                try:
                    await self._turn_worker_task
                except asyncio.CancelledError:
                    pass
            self._turn_worker_task = None
        await super().cleanup()

    async def _process_complete_turn(
        self, patient_text: str, turn_boundary_ms: int
    ) -> None:

        logger.bind(
            event="voice_turn_committed",
            conversation_id=self.conversation_id,
            turn_boundary_ms=turn_boundary_ms,
            **transcript_fields(patient_text),
        ).info("Patient voice turn committed")
        try:
            scheduling_started_at = time.perf_counter()
            result = await asyncio.to_thread(
                self.service.process_turn,
                self.conversation_id,
                patient_text,
            )
            scheduling_ms = round(
                (time.perf_counter() - scheduling_started_at) * 1000
            )
            logger.bind(
                event="voice_turn_completed",
                conversation_id=self.conversation_id,
                turn_id=result.get("message_id"),
                message_number=result.get("message_number"),
                next_action=result["engine_result"]["next_action"]["type"],
                changed_fields=sorted(result["state_patch"]),
                total_tokens=(
                    result["usage"]["input_tokens"]
                    + result["usage"]["output_tokens"]
                ),
                turn_boundary_ms=turn_boundary_ms,
                scheduling_ms=scheduling_ms,
            ).info("Voice scheduling turn completed")
            if self.rtvi is not None:
                await self.rtvi.send_server_message(
                    {
                        "type": "scheduling_turn",
                        "payload": {
                            **result,
                            "patient_text": patient_text,
                            "voice_timing": {
                                "turn_boundary_ms": turn_boundary_ms,
                                "scheduling_ms": scheduling_ms,
                            },
                        },
                    }
                )
            await self.push_frame(TTSSpeakFrame(text=result["assistant_message"]))
        except Exception as exc:
            logger.bind(
                event="voice_turn_failed",
                conversation_id=self.conversation_id,
                exception_type=type(exc).__name__,
            ).exception("Scheduling voice turn failed")
            try:
                await asyncio.to_thread(
                    self.service.finish_conversation,
                    self.conversation_id,
                    forced_outcome="SYSTEM_ERROR",
                )
            except Exception:
                logger.bind(
                    event="voice_failure_finalize_failed",
                    conversation_id=self.conversation_id,
                ).exception("Could not record failed conversation outcome")
            if self.rtvi is not None:
                await self.rtvi.send_server_message(
                    {
                        "type": "scheduling_error",
                        "payload": {"message": type(exc).__name__},
                    }
                )
            await self.push_frame(
                TTSSpeakFrame(
                    text=(
                        "I’m sorry, I couldn’t safely process that request. "
                        "Could you say it another way?"
                    )
                )
            )


def build_pipeline(
    *,
    transport: BaseTransport,
    vad: FrameProcessor,
    stt: Any,
    turn_detector: FrameProcessor,
    scheduler: FrameProcessor,
    tts: Any,
) -> Pipeline:
    """Build the observable STT -> scheduler -> TTS pipeline."""

    return Pipeline(
        [
            transport.input(),
            vad,
            stt,
            turn_detector,
            scheduler,
            tts,
            transport.output(),
        ]
    )


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    """Run one WebRTC scheduling conversation."""

    elevenlabs_key = _required_environment("ELEVENLABS_API_KEY")
    _required_environment("OPENAI_API_KEY")

    service = shared_conversation_service()
    agent_config = AgentConfigRepository(
        Path(__file__).parent / "example_flow.json"
    ).load()
    greeting = agent_config.first_message
    created = service.create_conversation()
    logger.bind(
        event="voice_session_started",
        conversation_id=created["conversation_id"],
        extractor_mode=service.extractor_mode,
        storage_mode=service.storage_mode,
        transport="webrtc",
    ).info("Voice scheduling session started")

    stt = ElevenLabsRealtimeSTTService(
        api_key=elevenlabs_key,
        # The local Silero VAD is already the source of truth for turn end.
        # Commit on its stop frame instead of waiting for a second remote VAD
        # (which could remain open until the browser muted its audio track).
        commit_strategy=CommitStrategy.MANUAL,
    )
    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(
            params=VADParams(start_secs=0.15, stop_secs=0.35)
        )
    )
    turn_detector = UserTurnProcessor(
        user_turn_strategies=UserTurnStrategies(
            stop=[
                SpeechTimeoutUserTurnStopStrategy(
                    user_speech_timeout=TURN_END_SILENCE_SECS,
                    wait_for_transcript=True,
                )
            ]
        ),
        user_turn_stop_timeout=TURN_END_FAILSAFE_SECS,
    )
    scheduler = SchedulingTurnProcessor(service, created["conversation_id"])
    tts = ElevenLabsTTSService(
        api_key=elevenlabs_key,
        settings=ElevenLabsTTSService.Settings(
            voice=os.getenv("ELEVENLABS_VOICE_ID", agent_config.voice_id)
        ),
    )
    pipeline = build_pipeline(
        transport=transport,
        vad=vad,
        stt=stt,
        turn_detector=turn_detector,
        scheduler=scheduler,
        tts=tts,
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )
    scheduler.rtvi = worker.rtvi

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        logger.bind(
            event="voice_client_ready",
            conversation_id=created["conversation_id"],
        ).info("Embedded voice client ready")
        await rtvi.send_server_message(
            {"type": "scheduling_greeting", "payload": {"text": greeting}}
        )
        await worker.queue_frames([TTSSpeakFrame(text=greeting)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.bind(
            event="voice_client_disconnected",
            conversation_id=created["conversation_id"],
        ).info("Voice client disconnected")
        try:
            evaluation = await asyncio.to_thread(
                service.finish_conversation, created["conversation_id"]
            )
            logger.bind(
                event="voice_session_finished",
                conversation_id=created["conversation_id"],
                outcome=evaluation["outcome"],
                safe=evaluation["safe"],
                total_tokens=evaluation["total_tokens"],
            ).info("Voice scheduling session finalized")
        except Exception:
            logger.bind(
                event="voice_session_finalize_failed",
                conversation_id=created["conversation_id"],
            ).exception("Could not finalize conversation evaluation")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Entry point invoked by the Pipecat development runner."""

    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
