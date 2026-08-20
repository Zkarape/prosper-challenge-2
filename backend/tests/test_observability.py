import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from observability import _ensure_extras, read_logs, redact_text, transcript_fields


class ObservabilityTests(unittest.TestCase):
    def test_secrets_are_redacted(self):
        value = redact_text(
            "Authorization: Bearer token.value password=hunter2 "
            "postgresql://prosper:private@localhost/db sk-example123456789"
        )
        self.assertNotIn("token.value", value)
        self.assertNotIn("hunter2", value)
        self.assertNotIn("prosper:private", value)
        self.assertNotIn("sk-example123456789", value)

    def test_transcripts_are_private_by_default_and_opt_in(self):
        with patch.dict(os.environ, {"LOG_INCLUDE_TRANSCRIPTS": "false"}):
            safe = transcript_fields("I need a private appointment")
        self.assertEqual(safe, {"transcript_chars": 28, "transcript_words": 5})
        self.assertNotIn("transcript", safe)

        with patch.dict(os.environ, {"LOG_INCLUDE_TRANSCRIPTS": "true"}):
            explicit = transcript_fields("consented local text")
        self.assertEqual(explicit["transcript"], "consented local text")

    def test_verbose_pipecat_text_is_filtered_in_private_mode(self):
        third_party = {
            "extra": {},
            "level": SimpleNamespace(no=10),
            "name": "pipecat.services.elevenlabs.stt",
        }
        structured = {
            "extra": {"component": "voice", "event": "voice_transcript_segment"},
            "level": SimpleNamespace(no=10),
            "name": "bot",
        }
        with patch.dict(os.environ, {"LOG_INCLUDE_TRANSCRIPTS": "false"}):
            self.assertFalse(_ensure_extras(third_party, "voice"))
            self.assertTrue(_ensure_extras(structured, "voice"))

    def test_log_reader_filters_and_normalizes_loguru_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "api.jsonl"
            payload = {
                "record": {
                    "time": {"repr": "2026-08-20T12:00:00+00:00"},
                    "level": {"name": "ERROR"},
                    "message": "Turn failed",
                    "exception": None,
                    "extra": {
                        "process": "api",
                        "component": "scheduling",
                        "event": "turn_failed",
                        "conversation_id": "conv_test",
                    },
                }
            }
            path.write_text(json.dumps(payload) + "\n")
            with patch.dict(os.environ, {"LOG_DIR": directory}):
                events = read_logs(level="ERROR", search="conv_test")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event"], "turn_failed")
            self.assertEqual(events[0]["fields"]["conversation_id"], "conv_test")


if __name__ == "__main__":
    unittest.main()
