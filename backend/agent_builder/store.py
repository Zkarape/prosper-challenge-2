"""Safe file-backed storage for the single demo agent configuration."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any

from .schema import AgentConfig, NodeType


class AgentConfigRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = RLock()

    def load(self) -> AgentConfig:
        with self._lock:
            return AgentConfig.from_dict(json.loads(self.path.read_text()))

    def save(self, value: dict[str, Any]) -> AgentConfig:
        config = AgentConfig.from_dict(value)
        encoded = json.dumps(config.to_dict(), indent=2, ensure_ascii=False) + "\n"
        temporary = self.path.with_suffix(".json.tmp")
        with self._lock:
            temporary.write_text(encoded)
            temporary.replace(self.path)
        return config

    @staticmethod
    def report(config: AgentConfig) -> dict[str, Any]:
        reachable = {config.initial_node}
        changed = True
        while changed:
            changed = False
            for node in config.nodes:
                if node.name not in reachable:
                    continue
                for edge in node.edges:
                    if edge.target not in reachable:
                        reachable.add(edge.target)
                        changed = True

        warnings = []
        unreachable = [node.name for node in config.nodes if node.name not in reachable]
        if unreachable:
            warnings.append(f"Unreachable nodes: {', '.join(unreachable)}")
        terminal_count = sum(
            node.type in {NodeType.END, NodeType.HANDOFF} for node in config.nodes
        )
        if terminal_count == 0:
            warnings.append("The workflow has no end or handoff node")

        return {
            "valid": True,
            "node_count": len(config.nodes),
            "edge_count": sum(len(node.edges) for node in config.nodes),
            "tool_count": len(config.tools),
            "reachable_node_count": len(reachable),
            "warnings": warnings,
        }
