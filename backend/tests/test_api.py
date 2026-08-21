from pathlib import Path
import json
import os
import sys
import time
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# API contract tests must not change behavior based on a developer's local key.
os.environ["EXTRACTOR_MODE"] = "local"
os.environ["DATABASE_URL"] = ""
os.environ["MIGRATION_DATABASE_URL"] = ""
os.environ["APP_ENV"] = "testing"
os.environ["ENABLE_DEBUG_LOG_API"] = "true"

from fastapi.testclient import TestClient

from api import app


class SchedulingApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def wait_for_evaluation(self, run_id: str) -> dict:
        for _ in range(200):
            payload = self.client.get(f"/api/evaluations/runs/{run_id}").json()
            if payload["status"] != "RUNNING":
                return payload
            time.sleep(0.01)
        self.fail("evaluation run did not finish")

    def wait_for_scalability_test(self, run_id: str) -> dict:
        for _ in range(400):
            payload = self.client.get(f"/api/scalability/runs/{run_id}").json()
            if payload["status"] != "RUNNING":
                return payload
            time.sleep(0.01)
        self.fail("scalability test did not finish")

    def test_health_and_text_turn_contract(self):
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.headers["x-request-id"].startswith("req_"))
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

    def test_local_structured_log_interface_and_browser_events(self):
        status = self.client.get("/api/system/logging")
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json()["transcripts_in_logs"])

        accepted = self.client.post(
            "/api/system/client-events",
            json={
                "level": "ERROR",
                "event": "browser_error",
                "message": "Test browser failure",
                "path": "/",
            },
        )
        self.assertEqual(accepted.status_code, 202)
        self.assertTrue(accepted.json()["accepted"])

        logs = self.client.get("/api/system/logs?limit=10&search=browser_error")
        self.assertEqual(logs.status_code, 200)
        self.assertIsInstance(logs.json()["events"], list)

    def test_unknown_conversation_returns_404(self):
        response = self.client.post(
            "/api/conversations/conv_missing/turns",
            json={"utterance": "Book a dental cleaning"},
        )
        self.assertEqual(response.status_code, 404)

    def test_conversation_can_be_finalized_and_evaluated(self):
        created = self.client.post("/api/conversations").json()
        conversation_id = created["conversation_id"]
        self.client.post(
            f"/api/conversations/{conversation_id}/turns",
            json={"utterance": "Where does Dr. Wei Lee work?"},
        )
        ended = self.client.post(
            f"/api/conversations/{conversation_id}/end",
            json={"outcome": "AUTO"},
        )
        self.assertEqual(ended.status_code, 200)
        self.assertEqual(ended.json()["outcome"], "INFORMATION_ANSWERED")
        self.assertTrue(ended.json()["safe"])

        evaluation = self.client.get(
            f"/api/conversations/{conversation_id}/evaluation"
        )
        self.assertEqual(evaluation.status_code, 200)
        self.assertIn("total_tokens", evaluation.json())

        summary = self.client.get("/api/evaluations/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertGreaterEqual(summary.json()["safe_completed_task_count"], 1)

    def test_context_management_dataset_is_available_to_the_ui(self):
        response = self.client.get("/api/evaluations/dataset")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["case_count"], 40)
        self.assertEqual(payload["defined_case_count"], 40)
        self.assertEqual(payload["manual_authored_case_count"], 2)
        self.assertEqual(payload["cases"][0]["test_case_id"], "case_001")

    def test_catalog_upload_builds_retrieval_index(self):
        catalog_path = BACKEND / "data" / "catalog.json"
        catalog = json.loads(catalog_path.read_text())
        status = self.client.get("/api/catalog")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["locations"], 8)

        uploaded = self.client.post("/api/catalog/upload", json={"catalog": catalog})
        self.assertEqual(uploaded.status_code, 200)
        self.assertEqual(uploaded.json()["retrieval"], "lexical_index_ready")

        search = self.client.post(
            "/api/catalog/search",
            json={"entity_type": "appointment_type", "query": "knee MRI"},
        )
        self.assertEqual(search.status_code, 200)
        self.assertGreaterEqual(search.json()["candidate_count"], 1)
        self.assertIn("latency_ms", search.json())

    def test_context_strategy_comparison_requires_real_model_usage(self):
        dataset = self.client.get("/api/evaluations/context-comparison/dataset")
        self.assertEqual(dataset.status_code, 200)
        self.assertEqual(dataset.json()["scenario_count"], 5)
        self.assertEqual(dataset.json()["turn_count"], 28)
        self.assertEqual(dataset.json()["repetitions"], 3)
        self.assertEqual(dataset.json()["turn_count_per_strategy"], 84)
        self.assertEqual(dataset.json()["total_patient_turns"], 252)
        self.assertFalse(dataset.json()["available"])

        rejected = self.client.post("/api/evaluations/context-comparison/runs")
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(
            rejected.json()["detail"],
            "CONTEXT_COMPARISON_REQUIRES_STRUCTURED_LLM",
        )

    def test_agent_workflow_is_available_and_invalid_edits_are_rejected(self):
        response = self.client.get("/api/agent")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["validation"]["valid"])
        self.assertEqual(payload["validation"]["node_count"], 7)
        self.assertEqual(payload["validation"]["tool_count"], 5)
        self.assertEqual(payload["config"]["initial_node"], "welcome")

        broken = {**payload["config"], "initial_node": "missing_node"}
        rejected = self.client.put("/api/agent", json={"config": broken})
        self.assertEqual(rejected.status_code, 422)
        self.assertIn("not defined", rejected.json()["detail"])

    def test_one_case_runs_through_all_four_graded_stages(self):
        response = self.client.post(
            "/api/evaluations/runs",
            json={"case_ids": ["case_001"], "extractor": "local"},
        )
        self.assertEqual(response.status_code, 202)
        payload = self.wait_for_evaluation(response.json()["run_id"])
        self.assertEqual(payload["summary"]["case_count"], 1)
        self.assertEqual(payload["summary"]["passed_case_count"], 1)
        self.assertEqual(
            [stage["id"] for stage in payload["cases"][0]["stages"]],
            ["extraction", "validation", "state", "engine"],
        )
        self.assertTrue(
            all(stage["status"] == "PASS" for stage in payload["cases"][0]["stages"])
        )

    def test_soft_preference_grades_the_best_candidate_tier(self):
        response = self.client.post(
            "/api/evaluations/runs",
            json={"case_ids": ["case_004"], "extractor": "local"},
        )
        self.assertEqual(response.status_code, 202)
        payload = self.wait_for_evaluation(response.json()["run_id"])
        engine_stage = next(
            stage
            for stage in payload["cases"][0]["stages"]
            if stage["id"] == "engine"
        )
        self.assertEqual(engine_stage["status"], "PASS")
        self.assertEqual(engine_stage["actual"]["blocker_codes"], [])
        self.assertEqual(
            engine_stage["actual"]["valid_candidate_ids"],
            ["appt_074:prov_018:loc_001"],
        )

    def test_all_40_cases_execute_and_latest_run_is_available(self):
        response = self.client.post(
            "/api/evaluations/runs",
            json={"extractor": "local"},
        )
        self.assertEqual(response.status_code, 202)
        payload = self.wait_for_evaluation(response.json()["run_id"])
        self.assertEqual(payload["summary"]["case_count"], 40)
        self.assertEqual(len(payload["cases"]), 40)
        self.assertEqual(
            payload["summary"]["passed_case_count"]
            + payload["summary"]["failed_case_count"],
            40,
        )

        latest = self.client.get("/api/evaluations/runs/latest")
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(latest.json()["run_id"], payload["run_id"])

    def test_100_session_scalability_burst_reports_real_measurements(self):
        response = self.client.post(
            "/api/scalability/runs",
            json={"target_sessions": 100},
        )
        self.assertEqual(response.status_code, 202)
        payload = self.wait_for_scalability_test(response.json()["run_id"])
        self.assertEqual(payload["status"], "COMPLETED")
        self.assertEqual(payload["summary"]["submitted_sessions"], 100)
        self.assertEqual(payload["summary"]["successful_sessions"], 100)
        self.assertEqual(payload["summary"]["unique_conversation_ids"], 100)
        self.assertTrue(payload["summary"]["isolated_state"])
        self.assertIn("OpenAI network latency and provider rate limits", payload["scope"]["excluded"])


if __name__ == "__main__":
    unittest.main()
