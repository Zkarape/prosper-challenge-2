from pathlib import Path
import os
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# API contract tests must not change behavior based on a developer's local key.
os.environ["EXTRACTOR_MODE"] = "local"

from fastapi.testclient import TestClient

from api import app


class SchedulingApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_health_and_text_turn_contract(self):
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertIn(health.json()["extractor_mode"], {"LOCAL_STRUCTURED", "OPENAI_STRUCTURED"})

        created = self.client.post("/api/conversations")
        self.assertEqual(created.status_code, 201)
        conversation_id = created.json()["conversation_id"]

        turn = self.client.post(
            f"/api/conversations/{conversation_id}/turns",
            json={
                "message_id": "api_message_1",
                "utterance": (
                    "I'm a new patient looking for the earliest dental cleaning "
                    "with Dr. Wei Lee. Richmond is a must."
                ),
            },
        )
        self.assertEqual(turn.status_code, 200)
        payload = turn.json()
        self.assertEqual(payload["message_id"], "api_message_1")
        self.assertEqual(payload["engine_result"]["decision"]["status"], "NO_EXACT_MATCH")
        self.assertEqual(payload["pending_offer"]["kind"], "ALTERNATIVE_LOCATION")
        self.assertEqual(
            [item["stage"] for item in payload["trace"]],
            ["Extract", "Resolve", "Rules", "Decision"],
        )

    def test_unknown_conversation_returns_404(self):
        response = self.client.post(
            "/api/conversations/conv_missing/turns",
            json={"utterance": "Book a dental cleaning"},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
