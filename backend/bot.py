"""Observable Pipecat voice adapter for the deterministic scheduler.

Runtime path:

    browser microphone -> WebRTC -> ElevenLabs final transcript
        -> structured LLM extraction -> deterministic scheduling engine
        -> checked response plan -> LLM response writer
        -> ElevenLabs TTS -> browser audio

Each processed voice turn is also sent to the embedded frontend as an RTVI
server message so extraction usage, state, rules, and the next action are
inspectable while the call is running.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    UserStoppedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
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
from scheduling.service import GREETING, PreparedTurn
from agent_builder import AgentConfigRepository


load_dotenv(Path(__file__).parent / ".env", override=True)
configure_logging("voice")
logger = get_logger("voice")

DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
DEFAULT_TTS_MODEL = "eleven_flash_v2_5"
VAD_STOP_SECS = float(os.getenv("VOICE_VAD_STOP_SECS", "0.20"))
TURN_END_SILENCE_SECS = float(os.getenv("VOICE_TURN_END_SILENCE_SECS", "0.20"))
TURN_END_FAILSAFE_SECS = 2.5
SPECULATION_MIN_WORDS = 4
SPECULATION_MIN_CHARS = 20
SPECULATION_STABILITY_SECS = float(
    os.getenv("VOICE_SPECULATION_STABILITY_SECS", "0.12")
)

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


def _spoken_text_key(text: str) -> str:
    return " ".join(part.strip(".,!?;:\"'()[]{}").casefold() for part in text.split())


@dataclass
class QueuedVoiceTurn:
    patient_text: str
    turn_boundary_ms: int
    speech_stopped_at: float
    message_id: str | None
    speculative_text: str | None
    preparation_task: asyncio.Task[PreparedTurn | None] | None


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
        self._speech_stopped_at: float | None = None
        self._speculative_text: str | None = None
        self._speculative_message_id: str | None = None
        self._preparation_task: asyncio.Task[PreparedTurn | None] | None = None
        self._preparation_started = False
        self._pending_audio: deque[dict[str, Any]] = deque()
        self._turn_queue: asyncio.Queue[QueuedVoiceTurn] = asyncio.Queue()
        self._turn_worker_task: asyncio.Task | None = None
        self._turn_in_flight = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, VADUserStoppedSpeakingFrame):
            # Silero emits this after its stop window. Backdate the marker so
            # the displayed latency includes the endpointing delay.
            self._speech_stopped_at = time.perf_counter() - VAD_STOP_SECS
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterimTranscriptionFrame):
            patient_text = frame.text.strip()
            if patient_text:
                await self._maybe_prepare_turn(patient_text)
            await self.push_frame(frame, direction)
            return

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
                await self._maybe_prepare_turn(" ".join(self._transcript_segments))
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
        work = QueuedVoiceTurn(
            patient_text=patient_text,
            turn_boundary_ms=turn_boundary_ms,
            speech_stopped_at=self._speech_stopped_at or time.perf_counter(),
            message_id=self._speculative_message_id,
            speculative_text=self._speculative_text,
            preparation_task=self._preparation_task,
        )
        self._speech_stopped_at = None
        self._speculative_text = None
        self._speculative_message_id = None
        self._preparation_task = None
        self._preparation_started = False

        # Never hold Pipecat's frame processor open while the LLM and database
        # work runs. Audio, STT and VAD frames must keep flowing. A single
        # consumer still preserves conversation turn order.
        await self._turn_queue.put(work)
        self._ensure_turn_worker()

    async def _maybe_prepare_turn(self, patient_text: str) -> None:
        """Start at most one side-effect-free preview for the current utterance."""

        if (
            self._turn_in_flight
            or not self._turn_queue.empty()
            or not hasattr(self.service, "prepare_turn")
        ):
            return
        if len(patient_text) < SPECULATION_MIN_CHARS:
            return
        if len(patient_text.split()) < SPECULATION_MIN_WORDS:
            return

        if self._preparation_task is not None:
            if self._preparation_task.done() or self._preparation_started:
                return
            # Use the newest interim if the provider call has not begun yet.
            self._preparation_task.cancel()
        else:
            self._preparation_started = False

        message_id = self._speculative_message_id or f"voice_{uuid4().hex[:12]}"
        self._speculative_text = patient_text
        self._speculative_message_id = message_id
        self._preparation_task = asyncio.create_task(
            self._prepare_after_stable_transcript(patient_text, message_id),
            name="speculative-scheduling-turn",
        )

    async def _prepare_after_stable_transcript(
        self, patient_text: str, message_id: str
    ) -> PreparedTurn | None:
        await asyncio.sleep(SPECULATION_STABILITY_SECS)
        self._preparation_started = True
        logger.bind(
            event="speculative_turn_started",
            conversation_id=self.conversation_id,
            turn_id=message_id,
            transcript_chars=len(patient_text),
        ).debug("Started a side-effect-free speculative turn")
        return await asyncio.to_thread(
            self.service.prepare_turn,
            self.conversation_id,
            patient_text,
            message_id=message_id,
        )

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
            work = await self._turn_queue.get()
            try:
                self._turn_in_flight = True
                await self._process_complete_turn(work)
            finally:
                self._turn_in_flight = False
                self._turn_queue.task_done()

    async def wait_for_pending_turns(self) -> None:
        """Wait until every completed speech turn has been processed."""

        await self._turn_queue.join()

    async def cleanup(self):
        if self._preparation_task is not None:
            self._preparation_task.cancel()
            self._preparation_task = None
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

    async def _process_complete_turn(self, work: QueuedVoiceTurn) -> None:
        patient_text = work.patient_text
        turn_boundary_ms = work.turn_boundary_ms

        logger.bind(
            event="voice_turn_committed",
            conversation_id=self.conversation_id,
            turn_boundary_ms=turn_boundary_ms,
            **transcript_fields(patient_text),
        ).info("Patient voice turn committed")
        try:
            scheduling_started_at = time.perf_counter()
            result = None
            speculation_reason = "not_started"
            if work.preparation_task is not None:
                if _spoken_text_key(work.speculative_text or "") == _spoken_text_key(
                    patient_text
                ):
                    prepared = await work.preparation_task
                    if prepared is not None:
                        result = await asyncio.to_thread(
                            self.service.commit_prepared_turn,
                            self.conversation_id,
                            patient_text,
                            prepared,
                        )
                    speculation_reason = "accepted" if result is not None else "stale"
                else:
                    work.preparation_task.cancel()
                    speculation_reason = "transcript_changed"
            if result is None:
                if work.message_id is None:
                    result = await asyncio.to_thread(
                        self.service.process_turn,
                        self.conversation_id,
                        patient_text,
                    )
                else:
                    result = await asyncio.to_thread(
                        self.service.process_turn,
                        self.conversation_id,
                        patient_text,
                        message_id=work.message_id,
                    )
                result["speculation"] = {
                    "used": False,
                    "reason": speculation_reason,
                }
            scheduling_ms = round(
                (time.perf_counter() - scheduling_started_at) * 1000
            )
            tts_requested_at = time.perf_counter()
            speech_end_to_tts_request_ms = round(
                (tts_requested_at - work.speech_stopped_at) * 1000
            )
            result["voice_timing"] = {
                "turn_boundary_ms": turn_boundary_ms,
                "scheduling_ms": scheduling_ms,
                "speech_end_to_tts_request_ms": speech_end_to_tts_request_ms,
                "speech_end_to_first_audio_ms": None,
            }
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
                speculation_used=result["speculation"]["used"],
            ).info("Voice scheduling turn completed")
            if self.rtvi is not None:
                await self.rtvi.send_server_message(
                    {
                        "type": "scheduling_turn",
                        "payload": {
                            **result,
                            "patient_text": patient_text,
                        },
                    }
                )
            self._pending_audio.append(
                {
                    "speech_stopped_at": work.speech_stopped_at,
                    "tts_requested_at": tts_requested_at,
                    "result": result,
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

    async def on_first_tts_audio(self) -> None:
        """Attach the user-visible latency when TTS emits its first audio chunk."""

        if not self._pending_audio:
            return
        pending = self._pending_audio.popleft()
        now = time.perf_counter()
        result = pending["result"]
        timing = result["voice_timing"]
        timing["tts_request_to_first_audio_ms"] = round(
            (now - pending["tts_requested_at"]) * 1000
        )
        timing["speech_end_to_first_audio_ms"] = round(
            (now - pending["speech_stopped_at"]) * 1000
        )
        logger.bind(
            event="voice_first_audio",
            conversation_id=self.conversation_id,
            turn_id=result.get("message_id"),
            **timing,
        ).info("Assistant first audio emitted")
        if self.rtvi is not None:
            await self.rtvi.send_server_message(
                {"type": "scheduling_turn", "payload": result}
            )


class FirstAudioLatencyProcessor(FrameProcessor):
    """Observe TTS output without changing the audio stream."""

    def __init__(self, scheduler: SchedulingTurnProcessor, **kwargs):
        super().__init__(**kwargs)
        self.scheduler = scheduler
        self._waiting_for_audio = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSStartedFrame):
            self._waiting_for_audio = True
        elif isinstance(frame, TTSAudioRawFrame) and self._waiting_for_audio:
            self._waiting_for_audio = False
            await self.scheduler.on_first_tts_audio()
        await self.push_frame(frame, direction)


def build_pipeline(
    *,
    transport: BaseTransport,
    vad: FrameProcessor,
    stt: Any,
    turn_detector: FrameProcessor,
    scheduler: FrameProcessor,
    tts: Any,
    latency_observer: FrameProcessor | None = None,
) -> Pipeline:
    """Build the observable STT -> scheduler -> TTS pipeline."""

    processors = [transport.input(), vad, stt, turn_detector, scheduler, tts]
    if latency_observer is not None:
        processors.append(latency_observer)
    processors.append(transport.output())
    return Pipeline(processors)


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
        response_writer_mode=service.response_writer_mode,
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
            params=VADParams(start_secs=0.15, stop_secs=VAD_STOP_SECS)
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
    latency_observer = FirstAudioLatencyProcessor(scheduler)
    tts = ElevenLabsTTSService(
        api_key=elevenlabs_key,
        settings=ElevenLabsTTSService.Settings(
            voice=os.getenv("ELEVENLABS_VOICE_ID", agent_config.voice_id),
            model=os.getenv("ELEVENLABS_TTS_MODEL", DEFAULT_TTS_MODEL),
        ),
    )
    pipeline = build_pipeline(
        transport=transport,
        vad=vad,
        stt=stt,
        turn_detector=turn_detector,
        scheduler=scheduler,
        tts=tts,
        latency_observer=latency_observer,
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
