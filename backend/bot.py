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

from pipecat.frames.frames import Frame, TranscriptionFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.elevenlabs.stt import (
    CommitStrategy,
    ElevenLabsRealtimeSTTService,
)
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

from scheduling import ConversationService
from scheduling.service import GREETING


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
    """Run each committed patient transcript through the checked turn service."""

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

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if not isinstance(frame, TranscriptionFrame):
            await self.push_frame(frame, direction)
            return

        patient_text = frame.text.strip()
        if not patient_text:
            return

        logger.info("Processing committed patient turn: {}", patient_text)
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
                    {"type": "scheduling_turn", "payload": result}
                )
            await self.push_frame(TTSSpeakFrame(text=result["assistant_message"]))
        except Exception as exc:
            logger.exception("Scheduling voice turn failed")
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
    stt: Any,
    scheduler: FrameProcessor,
    tts: Any,
) -> Pipeline:
    """Build the observable STT -> scheduler -> TTS pipeline."""

    return Pipeline(
        [
            transport.input(),
            stt,
            scheduler,
            tts,
            transport.output(),
        ]
    )


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    """Run one WebRTC scheduling conversation."""

    elevenlabs_key = _required_environment("ELEVENLABS_API_KEY")
    _required_environment("OPENAI_API_KEY")

    service = ConversationService.default()
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
    scheduler = SchedulingTurnProcessor(service, created["conversation_id"])
    tts = ElevenLabsTTSService(
        api_key=elevenlabs_key,
        settings=ElevenLabsTTSService.Settings(
            voice=os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_VOICE_ID)
        ),
    )
    pipeline = build_pipeline(
        transport=transport,
        stt=stt,
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
        await worker.queue_frames([TTSSpeakFrame(text=GREETING)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Voice client disconnected")
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
