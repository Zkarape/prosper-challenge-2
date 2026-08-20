"use client";

import { useEffect, useMemo, useState } from "react";

type NodeType = "conversation" | "subagent" | "tool" | "decision" | "handoff" | "end";
type EdgeType = "condition" | "success" | "failure" | "default";

type WorkflowTool = {
  name: string;
  description: string;
  first_message: string;
  implementation: string;
  kind: string;
  parameters: Record<string, unknown>;
  outcomes: string[];
};

type WorkflowEdge = {
  id: string;
  target: string;
  label: string;
  type: EdgeType;
  condition: string;
  function: string;
  description: string;
  properties: Record<string, unknown>;
  required: string[];
};

type WorkflowNode = {
  name: string;
  title: string;
  type: NodeType;
  description: string;
  position: { x: number; y: number };
  task_messages: { role: string; content: string }[];
  role_message: string | null;
  tools: string[];
  edges: WorkflowEdge[];
  pre_actions: Record<string, unknown>[];
  post_actions: Record<string, unknown>[];
  runtime_stages: string[];
  end: boolean;
};

type WorkflowConfig = {
  schema_version: string;
  name: string;
  description: string;
  voice_id: string;
  model: string;
  persona: string;
  initial_node: string;
  tools: WorkflowTool[];
  nodes: WorkflowNode[];
};

type Validation = {
  valid: boolean;
  node_count: number;
  edge_count: number;
  tool_count: number;
  reachable_node_count: number;
  warnings: string[];
};

type TraceTurn = {
  total_latency_ms: number;
  trace: { stage: string }[];
} | null;

const NODE_WIDTH = 226;
const NODE_HEIGHT = 116;
const nodeTypes: { value: NodeType; label: string; detail: string }[] = [
  { value: "conversation", label: "Conversation", detail: "Talk or collect information" },
  { value: "subagent", label: "Subagent", detail: "Focused prompt with scoped tools" },
  { value: "tool", label: "Tool", detail: "Guaranteed backend operation" },
  { value: "decision", label: "Decision", detail: "Silent deterministic routing" },
  { value: "handoff", label: "Handoff", detail: "Transfer to clinic staff" },
  { value: "end", label: "End", detail: "Finish the conversation" },
];

const typeGlyph: Record<NodeType, string> = {
  conversation: "◎",
  subagent: "✦",
  tool: "◆",
  decision: "◇",
  handoff: "↗",
  end: "■",
};

function edgePath(source: WorkflowNode, target: WorkflowNode) {
  const sx = source.position.x + NODE_WIDTH;
  const sy = source.position.y + NODE_HEIGHT / 2;
  const tx = target.position.x;
  const ty = target.position.y + NODE_HEIGHT / 2;
  const bend = Math.max(64, Math.abs(tx - sx) * 0.46);
  if (tx >= sx) return `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}`;
  const drop = Math.max(sy, ty) + 92;
  return `M ${sx} ${sy} C ${sx + 80} ${sy}, ${sx + 80} ${drop}, ${(sx + tx) / 2} ${drop} C ${tx - 80} ${drop}, ${tx - 80} ${ty}, ${tx} ${ty}`;
}

function slug(value: string) {
  return value.toLocaleLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "node";
}

export function AgentGraphEditor({ apiUrl, currentTurn }: { apiUrl: string; currentTurn: TraceTurn }) {
  const [config, setConfig] = useState<WorkflowConfig | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [status, setStatus] = useState<"loading" | "saved" | "dirty" | "saving" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setStatus("loading");
    setError(null);
    try {
      const response = await fetch(`${apiUrl.replace(/\/$/, "")}/api/agent`);
      if (!response.ok) throw new Error("Start the scheduling API to load the workflow.");
      const payload = await response.json() as { config: WorkflowConfig; validation: Validation };
      setConfig(payload.config);
      setValidation(payload.validation);
      setSelectedName((current) => current && payload.config.nodes.some((node) => node.name === current)
        ? current
        : payload.config.initial_node);
      setStatus("saved");
    } catch (cause) {
      setStatus("error");
      setError(cause instanceof Error ? cause.message : "The workflow could not be loaded.");
    }
  }

  useEffect(() => {
    let active = true;
    const base = apiUrl.replace(/\/$/, "");
    void fetch(`${base}/api/agent`)
      .then(async (response) => {
        if (!response.ok) throw new Error("Start the scheduling API to load the workflow.");
        return response.json() as Promise<{ config: WorkflowConfig; validation: Validation }>;
      })
      .then((payload) => {
        if (!active) return;
        setConfig(payload.config);
        setValidation(payload.validation);
        setSelectedName(payload.config.initial_node);
        setStatus("saved");
      })
      .catch((cause: unknown) => {
        if (!active) return;
        setStatus("error");
        setError(cause instanceof Error ? cause.message : "The workflow could not be loaded.");
      });
    return () => { active = false; };
  }, [apiUrl]);

  const selected = config?.nodes.find((node) => node.name === selectedName) ?? null;
  const ranStages = useMemo(
    () => new Set(currentTurn?.trace.map((event) => event.stage) ?? []),
    [currentTurn],
  );

  function updateConfig(updater: (current: WorkflowConfig) => WorkflowConfig) {
    setConfig((current) => current ? updater(current) : current);
    setStatus("dirty");
    setError(null);
  }

  function updateNode(updater: (node: WorkflowNode) => WorkflowNode) {
    if (!selectedName) return;
    updateConfig((current) => ({
      ...current,
      nodes: current.nodes.map((node) => node.name === selectedName ? updater(node) : node),
    }));
  }

  async function save() {
    if (!config || status === "saving") return;
    setStatus("saving");
    setError(null);
    try {
      const response = await fetch(`${apiUrl.replace(/\/$/, "")}/api/agent`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config }),
      });
      const payload = await response.json() as { config?: WorkflowConfig; validation?: Validation; detail?: string };
      if (!response.ok || !payload.config || !payload.validation) {
        throw new Error(payload.detail ?? "The workflow is not valid.");
      }
      setConfig(payload.config);
      setValidation(payload.validation);
      setStatus("saved");
    } catch (cause) {
      setStatus("error");
      setError(cause instanceof Error ? cause.message : "The workflow could not be saved.");
    }
  }

  function addNode(type: NodeType) {
    if (!config) return;
    const base = type === "end" ? "complete" : `new_${type}`;
    let name = base;
    let number = 2;
    while (config.nodes.some((node) => node.name === name)) name = `${base}_${number++}`;
    const node: WorkflowNode = {
      name,
      title: nodeTypes.find((item) => item.value === type)?.label ?? "New step",
      type,
      description: "Describe the single responsibility of this step.",
      position: { x: 120 + config.nodes.length * 36, y: 160 + config.nodes.length * 30 },
      task_messages: type === "decision" || type === "tool" ? [] : [{ role: "developer", content: "Describe what the assistant should do in this step." }],
      role_message: null,
      tools: [],
      edges: [],
      pre_actions: [],
      post_actions: type === "end" ? [{ type: "end_conversation" }] : [],
      runtime_stages: [],
      end: type === "end",
    };
    updateConfig((current) => ({ ...current, nodes: [...current.nodes, node] }));
    setSelectedName(name);
  }

  function deleteSelected() {
    if (!config || !selected || selected.name === config.initial_node) return;
    const next = config.nodes.find((node) => node.name !== selected.name)?.name ?? null;
    updateConfig((current) => ({
      ...current,
      nodes: current.nodes
        .filter((node) => node.name !== selected.name)
        .map((node) => ({ ...node, edges: node.edges.filter((edge) => edge.target !== selected.name) })),
    }));
    setSelectedName(next);
  }

  function beginDrag(event: React.PointerEvent, node: WorkflowNode) {
    if (event.button !== 0) return;
    event.preventDefault();
    const startClientX = event.clientX;
    const startClientY = event.clientY;
    const startX = node.position.x;
    const startY = node.position.y;
    const move = (next: PointerEvent) => {
      updateConfig((current) => ({
        ...current,
        nodes: current.nodes.map((item) => item.name === node.name ? {
          ...item,
          position: {
            x: Math.max(20, Math.round((startX + next.clientX - startClientX) / 10) * 10),
            y: Math.max(20, Math.round((startY + next.clientY - startClientY) / 10) * 10),
          },
        } : item),
      }));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  }

  if (!config) {
    return (
      <div className="page graph-page">
        <div className="workflow-loading">
          <span>{status === "loading" ? "Loading workflow…" : error}</span>
          {status === "error" && <button type="button" onClick={() => void load()}>Try again</button>}
        </div>
      </div>
    );
  }

  const allEdges = config.nodes.flatMap((node) => node.edges.map((edge) => ({ source: node, edge })));
  const maxX = Math.max(1260, ...config.nodes.map((node) => node.position.x + NODE_WIDTH + 80));
  const maxY = Math.max(780, ...config.nodes.map((node) => node.position.y + NODE_HEIGHT + 80));

  return (
    <div className="page graph-page workflow-page">
      <header className="workflow-header">
        <div>
          <div className="workflow-eyebrow"><span>Draft</span> Schema {config.schema_version}</div>
          <h2>{config.name}</h2>
          <p>{config.description}</p>
        </div>
        <div className="workflow-actions">
          <span className={`save-state save-state-${status}`}><i />{status === "dirty" ? "Unsaved changes" : status === "saving" ? "Saving…" : status === "error" ? "Needs attention" : "Saved locally"}</span>
          <button className="secondary-action" type="button" onClick={() => void load()}>Reset</button>
          <button className="primary-action" type="button" onClick={() => void save()} disabled={status === "saving" || status === "saved"}>Save workflow</button>
        </div>
      </header>

      {error && <div className="workflow-error" role="alert">{error}</div>}

      <div className="workflow-summary">
        <span><strong>{config.nodes.length}</strong> nodes</span>
        <span><strong>{allEdges.length}</strong> edges</span>
        <span><strong>{config.tools.length}</strong> tools</span>
        <span><strong>{validation?.reachable_node_count ?? "—"}</strong> reachable</span>
        {currentTurn && <span className="last-run">Last call turn · {currentTurn.total_latency_ms} ms</span>}
      </div>

      <div className="workflow-shell">
        <aside className="node-palette">
          <div><strong>Add a step</strong><small>Keep each node focused on one job.</small></div>
          {nodeTypes.map((item) => (
            <button key={item.value} type="button" onClick={() => addNode(item.value)}>
              <i className={`node-glyph type-${item.value}`}>{typeGlyph[item.value]}</i>
              <span><strong>{item.label}</strong><small>{item.detail}</small></span>
              <b>+</b>
            </button>
          ))}
          <div className="palette-note"><span>Design rule</span><p>Prompts may understand and speak. Server tools decide eligibility, availability, and booking.</p></div>
        </aside>

        <section className="workflow-viewport" aria-label="Voice agent workflow">
          <div className="workflow-canvas" style={{ width: maxX, height: maxY }}>
            <svg width={maxX} height={maxY} aria-hidden="true">
              <defs>
                <marker id="flow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                  <path d="M0,0 L8,4 L0,8 Z" fill="currentColor" />
                </marker>
              </defs>
              {allEdges.map(({ source, edge }) => {
                const target = config.nodes.find((node) => node.name === edge.target);
                if (!target) return null;
                return <path className={`workflow-edge edge-${edge.type}`} d={edgePath(source, target)} key={edge.id} markerEnd="url(#flow-arrow)" />;
              })}
            </svg>

            <div className="start-pill" style={{ left: Math.max(20, (config.nodes.find((node) => node.name === config.initial_node)?.position.x ?? 60) + 66), top: Math.max(10, (config.nodes.find((node) => node.name === config.initial_node)?.position.y ?? 60) - 44) }}>Start</div>

            {config.nodes.map((node) => {
              const ran = node.runtime_stages.some((stage) => ranStages.has(stage));
              return (
                <article
                  className={`workflow-node type-${node.type} ${selectedName === node.name ? "selected" : ""} ${ran ? "ran" : ""}`}
                  key={node.name}
                  style={{ left: node.position.x, top: node.position.y }}
                >
                  <div className="node-drag" onPointerDown={(event) => { setSelectedName(node.name); beginDrag(event, node); }}>
                    <i>{typeGlyph[node.type]}</i>
                    <span>{nodeTypes.find((item) => item.value === node.type)?.label}</span>
                    {ran && <b>Ran</b>}
                    <em>•••</em>
                  </div>
                  <button type="button" onClick={() => setSelectedName(node.name)}>
                    <strong>{node.title}</strong>
                    <small>{node.description}</small>
                  </button>
                  <footer><span>{node.tools.length ? `${node.tools.length} tool${node.tools.length === 1 ? "" : "s"}` : "No tools"}</span><span>{node.edges.length} path{node.edges.length === 1 ? "" : "s"}</span></footer>
                </article>
              );
            })}
          </div>
        </section>

        <NodeInspector
          config={config}
          node={selected}
          updateConfig={updateConfig}
          updateNode={updateNode}
          deleteNode={deleteSelected}
        />
      </div>
    </div>
  );
}

function NodeInspector({
  config,
  node,
  updateConfig,
  updateNode,
  deleteNode,
}: {
  config: WorkflowConfig;
  node: WorkflowNode | null;
  updateConfig: (updater: (current: WorkflowConfig) => WorkflowConfig) => void;
  updateNode: (updater: (node: WorkflowNode) => WorkflowNode) => void;
  deleteNode: () => void;
}) {
  if (!node) return <aside className="workflow-inspector"><div className="inspector-placeholder">Select a node to configure it.</div></aside>;
  const instruction = node.task_messages[0]?.content ?? "";

  function rename(value: string) {
    const nextName = slug(value);
    const oldName = node.name;
    updateConfig((current) => ({
      ...current,
      initial_node: current.initial_node === oldName ? nextName : current.initial_node,
      nodes: current.nodes.map((item) => item.name === oldName
        ? { ...item, name: nextName }
        : { ...item, edges: item.edges.map((edge) => edge.target === oldName ? { ...edge, target: nextName } : edge) }),
    }));
    setSelectedName(nextName);
  }

  function updateEdge(id: string, patch: Partial<WorkflowEdge>) {
    updateNode((current) => ({ ...current, edges: current.edges.map((edge) => edge.id === id ? { ...edge, ...patch } : edge) }));
  }

  function addEdge() {
    const target = config.nodes.find((item) => item.name !== node.name)?.name ?? node.name;
    const id = `${node.name}_edge_${Date.now().toString(36)}`;
    updateNode((current) => ({
      ...current,
      edges: [...current.edges, {
        id,
        target,
        label: "New path",
        type: "condition",
        condition: "Describe the observable condition for this transition.",
        function: id,
        description: "Continue when this condition is met.",
        properties: {},
        required: [],
      }],
    }));
  }

  return (
    <aside className="workflow-inspector">
      <header>
        <div><i className={`node-glyph type-${node.type}`}>{typeGlyph[node.type]}</i><span><small>Selected node</small><strong>{node.title}</strong></span></div>
        <button type="button" aria-label="Delete selected node" title={node.name === config.initial_node ? "The start node cannot be deleted" : "Delete node"} disabled={node.name === config.initial_node} onClick={deleteNode}>×</button>
      </header>

      <div className="inspector-scroll">
        <section className="inspector-section">
          <div className="section-title"><span>Identity</span>{node.name === config.initial_node && <b>Start node</b>}</div>
          <label>Display name<input value={node.title} onChange={(event) => updateNode((current) => ({ ...current, title: event.target.value }))} /></label>
          <label>Node ID<input value={node.name} onChange={(event) => rename(event.target.value)} /></label>
          <label>Node type<select value={node.type} onChange={(event) => updateNode((current) => ({ ...current, type: event.target.value as NodeType, end: event.target.value === "end", edges: event.target.value === "end" ? [] : current.edges }))}>{nodeTypes.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
          {node.name !== config.initial_node && <button className="set-start" type="button" onClick={() => updateConfig((current) => ({ ...current, initial_node: node.name }))}>Set as start node</button>}
        </section>

        <section className="inspector-section">
          <div className="section-title"><span>Behavior</span><small>One responsibility</small></div>
          {node.name === config.initial_node && (
            <label>First spoken message<textarea rows={3} value={config.first_message} onChange={(event) => updateConfig((current) => ({ ...current, first_message: event.target.value }))} /></label>
          )}
          <label>Description<textarea rows={3} value={node.description} onChange={(event) => updateNode((current) => ({ ...current, description: event.target.value }))} /></label>
          {node.type !== "decision" && node.type !== "tool" && (
            <label>Instructions<textarea rows={6} value={instruction} onChange={(event) => updateNode((current) => ({ ...current, task_messages: [{ role: "developer", content: event.target.value }, ...current.task_messages.slice(1)] }))} /></label>
          )}
        </section>

        <section className="inspector-section">
          <div className="section-title"><span>Tools</span><small>{node.tools.length} scoped</small></div>
          <div className="tool-list">
            {config.tools.map((tool) => {
              const enabled = node.tools.includes(tool.name);
              return (
                <div className={enabled ? "tool-option enabled" : "tool-option"} key={tool.name} title={tool.implementation}>
                  <input id={`tool-${node.name}-${tool.name}`} type="checkbox" checked={enabled} onChange={() => updateNode((current) => ({ ...current, tools: enabled ? current.tools.filter((name) => name !== tool.name) : [...current.tools, tool.name] }))} />
                  <label htmlFor={`tool-${node.name}-${tool.name}`}><strong>{tool.name.replaceAll("_", " ")}</strong><small>{tool.description}</small></label>
                </div>
              );
            })}
          </div>
        </section>

        {node.type !== "end" && (
          <section className="inspector-section">
            <div className="section-title"><span>Paths</span><button type="button" onClick={addEdge}>+ Add path</button></div>
            <div className="edge-editor-list">
              {node.edges.map((edge) => (
                <article key={edge.id}>
                  <header><i className={`edge-dot edge-${edge.type}`} /><input aria-label="Path label" value={edge.label} onChange={(event) => updateEdge(edge.id, { label: event.target.value })} /><button type="button" aria-label="Delete path" onClick={() => updateNode((current) => ({ ...current, edges: current.edges.filter((item) => item.id !== edge.id) }))}>×</button></header>
                  <div><select aria-label="Path type" value={edge.type} onChange={(event) => updateEdge(edge.id, { type: event.target.value as EdgeType, condition: event.target.value === "default" ? "" : edge.condition })}><option value="condition">Condition</option><option value="success">Success</option><option value="failure">Failure</option><option value="default">Fallback</option></select><span>→</span><select aria-label="Destination node" value={edge.target} onChange={(event) => updateEdge(edge.id, { target: event.target.value })}>{config.nodes.filter((item) => item.name !== node.name).map((item) => <option value={item.name} key={item.name}>{item.title}</option>)}</select></div>
                  {edge.type !== "default" && <textarea aria-label="Transition condition" rows={3} value={edge.condition} onChange={(event) => updateEdge(edge.id, { condition: event.target.value, description: event.target.value })} />}
                </article>
              ))}
              {node.edges.length === 0 && <p>No outgoing paths. Add one so the call can continue.</p>}
            </div>
          </section>
        )}
      </div>
    </aside>
  );
}
