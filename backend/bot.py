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
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from loguru import logger

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
from pipecat.workers.runner import WorkerRunner

from scheduling import ConversationService, shared_conversation_service
from scheduling.service import GREETING
from agent_builder import AgentConfigRepository


load_dotenv(Path(__file__).parent / ".env", override=True)

DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

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

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            patient_text = frame.text.strip()
            if patient_text:
                self._transcript_segments.append(patient_text)
                logger.info("Buffered patient speech segment: {}", patient_text)
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
        if not isinstance(frame, UserStoppedSpeakingFrame):
            return

        patient_text = " ".join(self._transcript_segments).strip()
        self._transcript_segments.clear()
        if not patient_text:
            return

        logger.info("Processing complete patient turn: {}", patient_text)
        try:
            result = await asyncio.to_thread(
                self.service.process_turn,
                self.conversation_id,
                patient_text,
            )
            logger.info(
                "Voice decision={} fields={} tokens={}",
                result["engine_result"]["next_action"]["type"],
                sorted(result["state_patch"]),
                result["usage"]["input_tokens"] + result["usage"]["output_tokens"],
            )
            if self.rtvi is not None:
                await self.rtvi.send_server_message(
                    {
                        "type": "scheduling_turn",
                        "payload": {**result, "patient_text": patient_text},
                    }
                )
            await self.push_frame(TTSSpeakFrame(text=result["assistant_message"]))
        except Exception as exc:
            logger.exception("Scheduling voice turn failed")
            try:
                await asyncio.to_thread(
                    self.service.finish_conversation,
                    self.conversation_id,
                    forced_outcome="SYSTEM_ERROR",
                )
            except Exception:
                logger.exception("Could not record failed conversation outcome")
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
    logger.info("Starting observable scheduler with {}", service.extractor_mode)

    stt = ElevenLabsRealtimeSTTService(
        api_key=elevenlabs_key,
        commit_strategy=CommitStrategy.VAD,
        settings=ElevenLabsRealtimeSTTService.Settings(
            vad_silence_threshold_secs=0.8,
            min_speech_duration_ms=120,
            min_silence_duration_ms=350,
        ),
    )
    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(
            params=VADParams(start_secs=0.15, stop_secs=0.35)
        )
    )
    turn_detector = UserTurnProcessor(user_turn_stop_timeout=8.0)
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
        logger.info("Embedded voice client ready")
        await rtvi.send_server_message(
            {"type": "scheduling_greeting", "payload": {"text": greeting}}
        )
        await worker.queue_frames([TTSSpeakFrame(text=greeting)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Voice client disconnected")
        try:
            evaluation = await asyncio.to_thread(
                service.finish_conversation, created["conversation_id"]
            )
            logger.info(
                "Conversation outcome={} safe={} total_tokens={}",
                evaluation["outcome"],
                evaluation["safe"],
                evaluation["total_tokens"],
            )
        except Exception:
            logger.exception("Could not finalize conversation evaluation")
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
