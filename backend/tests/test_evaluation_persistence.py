from pathlib import Path
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from evaluation import EvaluationRunner
from scheduling import Catalog, RuleBasedExtractor
from scheduling.storage import InMemoryConversationStore


class EvaluationPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog.from_json(BACKEND / "data" / "catalog.json")
        cls.extractor = RuleBasedExtractor(cls.catalog)
        cls.dataset_path = BACKEND / "tests" / "fixtures" / "context_management_eval.json"

    def runner(self, store):
        return EvaluationRunner(
            catalog=self.catalog,
            configured_extractor=self.extractor,
            dataset_path=self.dataset_path,
            run_store=store,
        )

    def test_new_runner_instance_can_load_a_saved_run(self):
        store = InMemoryConversationStore()
        completed = self.runner(store).run(
            case_ids=["case_001"], extractor="local"
        )

        restarted_runner = self.runner(store)

        self.assertEqual(restarted_runner.get(completed["run_id"]), completed)
        self.assertEqual(restarted_runner.latest(), completed)


if __name__ == "__main__":
    unittest.main()
