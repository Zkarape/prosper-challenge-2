from pathlib import Path
import sys
import time
import unittest
from unittest.mock import AsyncMock


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from bot import (
    FirstAudioLatencyProcessor,
    GREETING,
    TURN_END_FAILSAFE_SECS,
    TURN_END_SILENCE_SECS,
    SchedulingTurnProcessor,
    build_pipeline,
)
from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class FakeTransport:
    def __init__(self, input_processor, output_processor):
        self._input = input_processor
        self._output = output_processor

    def input(self):
        return self._input

    def output(self):
        return self._output


class VoicePipelineTests(unittest.TestCase):
    def test_pipeline_sends_final_transcript_to_scheduler_then_tts(self):
        processors = {
            name: FrameProcessor(name=name)
            for name in (
                "transport_input",
                "vad",
                "stt",
                "turn_detector",
                "scheduler",
                "tts",
                "transport_output",
            )
        }
        pipeline = build_pipeline(
            transport=FakeTransport(
                processors["transport_input"], processors["transport_output"]
            ),
            vad=processors["vad"],
            stt=processors["stt"],
            turn_detector=processors["turn_detector"],
            scheduler=processors["scheduler"],
            tts=processors["tts"],
        )

        self.assertEqual(
            [processor.name for processor in pipeline.processors[1:-1]],
            [
                "transport_input",
                "vad",
                "stt",
                "turn_detector",
                "scheduler",
                "tts",
                "transport_output",
            ],
        )

    def test_voice_agent_starts_open_ended(self):
        self.assertEqual(
            GREETING,
            "Hi, I’m the clinic’s scheduling assistant. How can I help you today?",
        )

    def test_turn_detection_does_not_leave_the_patient_waiting(self):
        self.assertLessEqual(TURN_END_SILENCE_SECS, 0.8)
        self.assertLessEqual(TURN_END_FAILSAFE_SECS, 2.5)


class VoiceTurnPublishingTests(unittest.IsolatedAsyncioTestCase):
    async def test_segments_are_combined_before_publishing_one_patient_turn(self):
        result = {
            "assistant_message": "Are you a new or existing patient?",
            "engine_result": {"next_action": {"type": "ASK_REQUIRED_FIELD"}},
            "state_patch": {"appointment_type": {"raw_text": "knee MRI"}},
            "usage": {"input_tokens": 120, "output_tokens": 24},
        }

        class Service:
            def process_turn(self, conversation_id, patient_text):
                self.call = (conversation_id, patient_text)
                return result

        class RTVI:
            def __init__(self):
                self.messages = []

            async def send_server_message(self, message):
                self.messages.append(message)

        service = Service()
        rtvi = RTVI()
        processor = SchedulingTurnProcessor(service, "conversation_test")
        processor.rtvi = rtvi
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            TranscriptionFrame(
                text="I want",
                user_id="patient",
                timestamp="2026-08-19T00:00:00Z",
                finalized=False,
            ),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(
            TranscriptionFrame(
                text="to book a knee MRI",
                user_id="patient",
                timestamp="2026-08-19T00:00:01Z",
                finalized=False,
            ),
            FrameDirection.DOWNSTREAM,
        )

        self.assertFalse(hasattr(service, "call"))

        await processor.process_frame(
            UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.wait_for_pending_turns()

        self.assertEqual(
            service.call, ("conversation_test", "I want to book a knee MRI")
        )
        self.assertEqual(rtvi.messages[0]["type"], "scheduling_turn")
        self.assertEqual(
            rtvi.messages[0]["payload"]["patient_text"],
            "I want to book a knee MRI",
        )
        self.assertEqual(rtvi.messages[0]["payload"]["usage"]["input_tokens"], 120)
        spoken = next(
            call.args[0]
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], TTSSpeakFrame)
        )
        self.assertIsInstance(spoken, TTSSpeakFrame)
        self.assertEqual(spoken.text, result["assistant_message"])

        latency_observer = FirstAudioLatencyProcessor(processor)
        latency_observer.push_frame = AsyncMock()
        await latency_observer.process_frame(
            TTSStartedFrame(), FrameDirection.DOWNSTREAM
        )
        await latency_observer.process_frame(
            TTSAudioRawFrame(audio=b"\x00\x00", sample_rate=24000, num_channels=1),
            FrameDirection.DOWNSTREAM,
        )
        updated = rtvi.messages[-1]["payload"]
        self.assertIsNotNone(
            updated["voice_timing"]["speech_end_to_first_audio_ms"]
        )
        await latency_observer.cleanup()
        await processor.cleanup()

    async def test_matching_interim_transcript_uses_prepared_turn(self):
        result = {
            "assistant_message": "Are you a new or existing patient?",
            "engine_result": {"next_action": {"type": "ASK_REQUIRED_FIELD"}},
            "state_patch": {"appointment_type": {"raw_text": "knee MRI"}},
            "usage": {"input_tokens": 120, "output_tokens": 24},
            "message_id": "voice_preview",
            "message_number": 1,
            "speculation": {"used": True},
        }

        class Service:
            def prepare_turn(self, conversation_id, patient_text, *, message_id):
                self.prepared = (conversation_id, patient_text, message_id)
                return object()

            def commit_prepared_turn(self, conversation_id, patient_text, prepared):
                self.committed = (conversation_id, patient_text, prepared)
                return result

            def process_turn(self, *args, **kwargs):
                raise AssertionError("matching speculation should avoid a second model path")

        service = Service()
        processor = SchedulingTurnProcessor(service, "conversation_test")
        processor.push_frame = AsyncMock()
        transcript = "I want to book a knee MRI"

        await processor.process_frame(
            InterimTranscriptionFrame(
                text=transcript,
                user_id="patient",
                timestamp="2026-08-21T00:00:00Z",
            ),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(
            TranscriptionFrame(
                text=f"{transcript}.",
                user_id="patient",
                timestamp="2026-08-21T00:00:01Z",
                finalized=True,
            ),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(
            UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.wait_for_pending_turns()

        self.assertEqual(service.prepared[1], f"{transcript}.")
        self.assertEqual(service.committed[1], f"{transcript}.")
        spoken = next(
            call.args[0]
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], TTSSpeakFrame)
        )
        self.assertEqual(spoken.text, result["assistant_message"])
        await processor.cleanup()

    async def test_slow_scheduling_does_not_block_later_audio_frames(self):
        result = {
            "assistant_message": "Next question",
            "engine_result": {"next_action": {"type": "ASK_REQUIRED_FIELD"}},
            "state_patch": {},
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }

        class SlowService:
            def __init__(self):
                self.calls = []

            def process_turn(self, conversation_id, patient_text):
                self.calls.append(patient_text)
                time.sleep(0.05)
                return result

        service = SlowService()
        processor = SchedulingTurnProcessor(service, "conversation_test")
        processor.push_frame = AsyncMock()

        for text in ("first request", "second request"):
            await processor.process_frame(
                TranscriptionFrame(
                    text=text,
                    user_id="patient",
                    timestamp="2026-08-19T00:00:00Z",
                    finalized=True,
                ),
                FrameDirection.DOWNSTREAM,
            )
            started = time.perf_counter()
            await processor.process_frame(
                UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
            )
            self.assertLess(time.perf_counter() - started, 0.02)

        await processor.wait_for_pending_turns()
        self.assertEqual(service.calls, ["first request", "second request"])
        await processor.cleanup()


if __name__ == "__main__":
    unittest.main()
