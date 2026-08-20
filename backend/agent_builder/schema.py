"""Declarative, editor-friendly contract for a Prosper voice workflow.

The contract keeps Pipecat Flows' native concepts (task messages, functions and
actions) while adding the small amount of presentation metadata a visual editor
needs.  Node and edge types are deliberately platform-neutral so a reviewer can
understand the flow without knowing Pipecat internals.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
DEFAULT_MODEL = "gpt-4o"


class NodeType(str, Enum):
    CONVERSATION = "conversation"
    SUBAGENT = "subagent"
    TOOL = "tool"
    DECISION = "decision"
    HANDOFF = "handoff"
    END = "end"


class EdgeType(str, Enum):
    CONDITION = "condition"
    SUCCESS = "success"
    FAILURE = "failure"
    DEFAULT = "default"


@dataclass(frozen=True)
class Position:
    x: int = 80
    y: int = 80

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "Position":
        value = value or {}
        return cls(x=int(value.get("x", 80)), y=int(value.get("y", 80)))


@dataclass(frozen=True)
class ToolDefinition:
    """A named capability and the application seam that implements it."""

    name: str
    description: str
    implementation: str
    kind: str = "server"
    parameters: dict[str, Any] = field(default_factory=dict)
    outcomes: list[str] = field(default_factory=lambda: ["success", "failure"])

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ToolDefinition":
        return cls(
            name=value["name"],
            description=value["description"],
            implementation=value["implementation"],
            kind=value.get("kind", "server"),
            parameters=value.get("parameters", {}),
            outcomes=value.get("outcomes", ["success", "failure"]),
        )


@dataclass(frozen=True)
class Edge:
    """A visible transition and, for Pipecat, an optional callable function."""

    id: str
    target: str
    label: str
    type: EdgeType = EdgeType.CONDITION
    condition: str = ""
    function: str = ""
    description: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, source: str, index: int) -> "Edge":
        function = value.get("function", "")
        label = value.get("label") or value.get("description") or function or "Continue"
        return cls(
            id=value.get("id", f"{source}_edge_{index + 1}"),
            target=value["target"],
            label=label,
            type=EdgeType(value.get("type", "condition")),
            condition=value.get("condition", value.get("description", "")),
            function=function,
            description=value.get("description", value.get("condition", "")),
            properties=value.get("properties", {}),
            required=value.get("required", []),
        )


@dataclass(frozen=True)
class Node:
    """One focused conversational, decision, tool, handoff, or terminal step."""

    name: str
    title: str
    type: NodeType = NodeType.CONVERSATION
    description: str = ""
    position: Position = field(default_factory=Position)
    task_messages: list[dict[str, Any]] = field(default_factory=list)
    role_message: Optional[str] = None
    tools: list[str] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    pre_actions: list[dict[str, Any]] = field(default_factory=list)
    post_actions: list[dict[str, Any]] = field(default_factory=list)
    runtime_stages: list[str] = field(default_factory=list)
    end: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Node":
        name = value["name"]
        node_type = NodeType("end" if value.get("end") else value.get("type", "conversation"))
        return cls(
            name=name,
            title=value.get("title", name.replace("_", " ").title()),
            type=node_type,
            description=value.get("description", ""),
            position=Position.from_dict(value.get("position")),
            task_messages=value.get("task_messages", []),
            role_message=value.get("role_message"),
            tools=value.get("tools", []),
            edges=[
                Edge.from_dict(edge, source=name, index=index)
                for index, edge in enumerate(value.get("edges", []))
            ],
            pre_actions=value.get("pre_actions", []),
            post_actions=value.get("post_actions", []),
            runtime_stages=value.get("runtime_stages", []),
            end=value.get("end", node_type == NodeType.END),
        )


@dataclass(frozen=True)
class AgentConfig:
    """A versioned voice-agent workflow that can be edited and compiled."""

    name: str
    initial_node: str
    nodes: list[Node]
    persona: str = ""
    description: str = ""
    first_message: str = "Hi, I’m the clinic’s scheduling assistant. How can I help you today?"
    voice_id: str = DEFAULT_VOICE_ID
    model: str = DEFAULT_MODEL
    schema_version: str = "2.0"
    tools: list[ToolDefinition] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentConfig":
        config = cls(
            name=value["name"],
            initial_node=value["initial_node"],
            nodes=[Node.from_dict(node) for node in value["nodes"]],
            persona=value.get("persona", ""),
            description=value.get("description", ""),
            first_message=value.get(
                "first_message",
                "Hi, I’m the clinic’s scheduling assistant. How can I help you today?",
            ),
            voice_id=value.get("voice_id", DEFAULT_VOICE_ID),
            model=value.get("model", DEFAULT_MODEL),
            schema_version=value.get("schema_version", "1.0"),
            tools=[ToolDefinition.from_dict(tool) for tool in value.get("tools", [])],
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.nodes:
            raise ValueError("Agent has no nodes")
        if not self.first_message.strip():
            raise ValueError("Agent first_message cannot be empty")
        names = [node.name for node in self.nodes]
        if len(names) != len(set(names)):
            raise ValueError("Node names must be unique")
        if self.initial_node not in names:
            raise ValueError(f"Initial node '{self.initial_node}' is not defined")

        tool_names = [tool.name for tool in self.tools]
        if len(tool_names) != len(set(tool_names)):
            raise ValueError("Tool names must be unique")
        known_tools = set(tool_names)
        known_nodes = set(names)
        edge_ids: set[str] = set()
        for node in self.nodes:
            if not node.name.strip() or not node.title.strip():
                raise ValueError("Every node requires a name and title")
            missing_tools = set(node.tools) - known_tools
            if missing_tools:
                raise ValueError(
                    f"Node '{node.name}' references unknown tools: {sorted(missing_tools)}"
                )
            if node.type == NodeType.END and node.edges:
                raise ValueError(f"End node '{node.name}' cannot have outgoing edges")
            default_edges = [edge for edge in node.edges if edge.type == EdgeType.DEFAULT]
            if len(default_edges) > 1:
                raise ValueError(f"Node '{node.name}' has more than one default edge")
            for edge in node.edges:
                if edge.id in edge_ids:
                    raise ValueError(f"Edge id '{edge.id}' must be unique")
                edge_ids.add(edge.id)
                if edge.target not in known_nodes:
                    raise ValueError(
                        f"Edge '{edge.id}' in '{node.name}' targets unknown node '{edge.target}'"
                    )
                if edge.type == EdgeType.CONDITION and not edge.condition.strip():
                    raise ValueError(f"Conditional edge '{edge.id}' requires a condition")

    def to_dict(self) -> dict[str, Any]:
        return _enum_values(asdict(self))


def _enum_values(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _enum_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_enum_values(item) for item in value]
    return value
