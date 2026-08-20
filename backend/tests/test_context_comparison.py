from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from evaluation import ContextComparisonRunner
from extraction.llm_extractor import ExtractionResult, ExtractionTelemetry
from scheduling.catalog import Catalog
from scheduling.extractor import RuleBasedExtractor
from scheduling.storage import InMemoryConversationStore


class MeasuredLocalExtractor:
    """Repeatable test double with token counts that reflect its input."""

    mode = "TEST_MEASURED"
    model = "test-measured-model"

    def __init__(self, catalog: Catalog, strategy: str = "compact"):
        self.local = RuleBasedExtractor(catalog)
        self.context_strategy = strategy
        self.calls = 0

    def for_context_strategy(self, strategy: str) -> "MeasuredLocalExtractor":
        return MeasuredLocalExtractor(self.local.catalog, strategy)

    def extract(self, **kwargs):
        self.calls += 1
        parsed = self.local.extract(**kwargs).parsed
        compact_payload = {
            "patient_request": kwargs.get("patient_request"),
            "pending_offer": kwargs.get("pending_offer"),
            "latest_patient_utterance": kwargs.get("patient_text"),
        }
        if self.context_strategy == "bounded_recent":
            compact_payload["recent_context"] = (
                kwargs.get("conversation_history") or []
            )[-2:]
        elif self.context_strategy == "full_history":
            compact_payload["conversation_history"] = kwargs.get(
                "conversation_history"
            ) or []
        input_tokens = 800 + len(json.dumps(compact_payload)) // 4
        return ExtractionResult(
            parsed,
            ExtractionTelemetry(
                self.model,
                "test",
                "test",
                input_tokens,
                0,
                50,
                10,
                f"{self.context_strategy}_{self.calls}",
                "completed",
            ),
        )


class ContextComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog.from_json(BACKEND / "data" / "catalog.json")
        cls.dataset = BACKEND / "tests" / "fixtures" / "context_strategy_eval.json"

    def test_paired_runner_measures_savings_without_accuracy_loss(self):
        store = InMemoryConversationStore()
        runner = ContextComparisonRunner(
            catalog=self.catalog,
            configured_extractor=MeasuredLocalExtractor(self.catalog),
            dataset_path=self.dataset,
            run_store=store,
            repetitions=1,
        )

        result = runner.run(run_id="context_eval_test")

        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["strategies"]["compact"]["passed_scenarios"], 5)
        self.assertEqual(
            result["strategies"]["bounded_recent"]["passed_scenarios"], 5
        )
        self.assertEqual(result["strategies"]["full_history"]["passed_scenarios"], 5)
        self.assertGreater(
            result["strategies"]["full_history"]["input_tokens"],
            result["strategies"]["bounded_recent"]["input_tokens"],
        )
        self.assertEqual(
            result["comparison"]["conclusion"],
            "SUPPORTED_WITHIN_BENCHMARK",
        )
        self.assertEqual(
            store.latest_context_comparison()["run_id"], "context_eval_test"
        )
        self.assertIsNone(store.latest_evaluation_run())

    def test_non_llm_extractor_cannot_claim_a_context_comparison(self):
        runner = ContextComparisonRunner(
            catalog=self.catalog,
            configured_extractor=RuleBasedExtractor(self.catalog),
            dataset_path=self.dataset,
            repetitions=1,
        )
        self.assertFalse(runner.available)
        with self.assertRaisesRegex(
            ValueError, "CONTEXT_COMPARISON_REQUIRES_STRUCTURED_LLM"
        ):
            runner.run()


if __name__ == "__main__":
    unittest.main()
