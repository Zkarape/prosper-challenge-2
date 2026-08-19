"""Pipecat voice transport around the shared deterministic turn service.

Speech is only input/output here:

    microphone -> ElevenLabs STT -> ConversationService -> ElevenLabs TTS

The OpenAI model inside ``ConversationService`` extracts observations using a
strict schema. It never writes bookings or selects catalog records.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from pipecat.frames.frames import Frame, TranscriptionFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
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


class SchedulingTurnProcessor(FrameProcessor):
    """Convert final transcripts into grounded text for TTS."""

    def __init__(
        self,
        service: ConversationService,
        conversation_id: str,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.service = service
        self.conversation_id = conversation_id

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            patient_text = frame.text.strip()
            if not patient_text:
                return
            logger.info("Processing final patient transcript")
            try:
                result = await asyncio.to_thread(
                    self.service.process_turn,
                    self.conversation_id,
                    patient_text,
                )
                response = result["assistant_message"]
                logger.info(
                    "Scheduling decision: {}",
                    result["engine_result"]["next_action"]["type"],
                )
            except Exception:
                logger.exception("Scheduling turn failed")
                response = (
                    "I’m sorry, I couldn’t safely process that request. "
                    "Could you say it again?"
                )
            await self.push_frame(TTSSpeakFrame(text=response))
            return
        await self.push_frame(frame, direction)


async def run_bot(
    transport: BaseTransport,
    runner_args: RunnerArguments,
    service: ConversationService | None = None,
) -> None:
    service = service or ConversationService.default()
    created = service.create_conversation()
    conversation_id = created["conversation_id"]
    logger.info("Starting deterministic scheduler with {}", service.extractor_mode)

    stt = ElevenLabsRealtimeSTTService(api_key=os.environ["ELEVENLABS_API_KEY"])
    tts = ElevenLabsTTSService(
        api_key=os.environ["ELEVENLABS_API_KEY"],
        settings=ElevenLabsTTSService.Settings(
            voice=os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_VOICE_ID)
        ),
    )
    scheduler = SchedulingTurnProcessor(service, conversation_id)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            scheduler,
            tts,
            transport.output(),
        ]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected — greeting from deterministic scheduler")
        await worker.queue_frames([TTSSpeakFrame(text=GREETING)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Entry point invoked by the Pipecat dev runner and Pipecat Cloud."""

    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
