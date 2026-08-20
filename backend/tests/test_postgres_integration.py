from datetime import date
import os
from pathlib import Path
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scheduling import Catalog, ConversationService, RuleBasedExtractor
from scheduling.storage import PostgresRuntimeStore, load_default_agent_config


@unittest.skipUnless(
    os.getenv("TEST_DATABASE_URL"),
    "set TEST_DATABASE_URL to run PostgreSQL integration tests",
)
class PostgresRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog.from_json(BACKEND / "data" / "catalog.json")

    def service(self):
        store = PostgresRuntimeStore(
            os.environ["TEST_DATABASE_URL"],
            catalog=self.catalog,
            clinic_id="postgres_integration_test",
            pool_size=2,
        )
        store.sync_configuration(
            catalog=self.catalog,
            agent_config=load_default_agent_config(),
        )
        return ConversationService(
            self.catalog,
            extractor=RuleBasedExtractor(self.catalog),
            today_provider=lambda: date(2026, 8, 19),
            store=store,
        )

    def test_another_worker_continues_and_books_the_same_conversation(self):
        first_worker = self.service()
        conversation_id = first_worker.create_conversation()["conversation_id"]
        offered = first_worker.process_turn(
            conversation_id,
            "I'm a new patient. Book a dental cleaning with Dr. Wei Lee at "
            "Mission District, earliest available.",
            message_id="database_turn_1",
        )
        self.assertEqual(offered["pending_offer"]["kind"], "SLOT_OPTIONS")
        first_worker.store.pool.close()

        second_worker = self.service()
        selected = second_worker.process_turn(
            conversation_id, "First", message_id="database_turn_2"
        )
        self.assertEqual(selected["pending_offer"]["kind"], "CONFIRM_BOOKING")
        second_worker.store.pool.close()

        third_worker = self.service()
        confirmed = third_worker.process_turn(
            conversation_id, "Yes, book it", message_id="database_turn_3"
        )
        self.assertEqual(confirmed["booking"]["status"], "confirmed")

        retried = third_worker.process_turn(
            conversation_id,
            "Different text must be ignored",
            message_id="database_turn_3",
        )
        self.assertEqual(retried, confirmed)
        self.assertEqual(third_worker.get_conversation(conversation_id).message_number, 3)
        third_worker.store.pool.close()


if __name__ == "__main__":
    unittest.main()
