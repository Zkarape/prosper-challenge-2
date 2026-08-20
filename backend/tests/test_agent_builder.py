from pathlib import Path
import json
import sys
import tempfile
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from agent_builder import AgentBuilder, AgentConfig, AgentConfigRepository


class AgentBuilderTests(unittest.TestCase):
    def setUp(self):
        self.value = json.loads((BACKEND / "example_flow.json").read_text())

    def test_example_workflow_validates_and_compiles(self):
        config = AgentConfig.from_dict(self.value)
        report = AgentConfigRepository.report(config)
        self.assertEqual(report["node_count"], 7)
        self.assertEqual(report["edge_count"], 11)
        self.assertEqual(report["reachable_node_count"], 7)
        self.assertEqual(report["warnings"], [])
        self.assertEqual(AgentBuilder(config).build_initial_node()["name"], "welcome")

    def test_repository_round_trip_preserves_editor_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.json"
            path.write_text(json.dumps(self.value))
            repository = AgentConfigRepository(path)
            saved = repository.save(repository.load().to_dict())
            self.assertEqual(saved.schema_version, "2.0")
            self.assertEqual(saved.nodes[0].position.x, 60)
            self.assertEqual(saved.tools[0].implementation, "extraction.OpenAIExtractor.extract")

    def test_invalid_tool_reference_is_rejected(self):
        self.value["nodes"][0]["tools"] = ["invented_tool"]
        with self.assertRaisesRegex(ValueError, "unknown tools"):
            AgentConfig.from_dict(self.value)


if __name__ == "__main__":
    unittest.main()
