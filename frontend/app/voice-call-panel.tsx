"use client";

import { useCallback, useEffect, useState } from "react";
import { PipecatClient, RTVIEvent, type TransportState } from "@pipecat-ai/client-js";
import {
  PipecatClientAudio,
  PipecatClientMicToggle,
  PipecatClientProvider,
  VoiceVisualizer,
  usePipecatClient,
  usePipecatClientTransportState,
  usePipecatConversation,
  useRTVIClientEvent,
} from "@pipecat-ai/client-react";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";

type VoiceCallPanelProps = {
  endpoint: string;
  onSchedulingTurn: (turn: unknown) => void;
};

function connectionLabel(state: TransportState): string {
  switch (state) {
    case "ready":
      return "Live";
    case "authenticating":
    case "authenticated":
    case "connecting":
      return "Connecting";
    case "disconnecting":
      return "Ending";
    case "error":
      return "Connection error";
    default:
      return "Not connected";
  }
}

function readablePart(value: unknown): string {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (!value || typeof value !== "object") return "";
  if ("spoken" in value || "unspoken" in value) {
    const output = value as { spoken?: unknown; unspoken?: unknown };
    return `${typeof output.spoken === "string" ? output.spoken : ""}${
      typeof output.unspoken === "string" ? output.unspoken : ""
    }`;
  }
  return "";
}

function unwrapServerMessage(value: unknown): { type?: unknown; payload?: unknown } | null {
  if (!value || typeof value !== "object") return null;
  const message = value as { type?: unknown; payload?: unknown; data?: unknown };
  if (typeof message.type === "string") return message;
  if (message.data && typeof message.data === "object") {
    return message.data as { type?: unknown; payload?: unknown };
  }
  return null;
}

function VoiceCallContent({ endpoint, onSchedulingTurn }: VoiceCallPanelProps) {
  const client = usePipecatClient();
  const state = usePipecatClientTransportState();
  const { messages } = usePipecatConversation();
  const [error, setError] = useState<string | null>(null);
  const connected = state === "ready" || state === "connected";
  const busy = ["authenticating", "authenticated", "connecting", "disconnecting"].includes(state);

  const handleServerMessage = useCallback((value: unknown) => {
    const message = unwrapServerMessage(value);
    if (message?.type === "scheduling_turn") {
      onSchedulingTurn(message.payload);
    } else if (message?.type === "scheduling_error") {
      setError("The scheduling engine could not safely process that turn.");
    }
  }, [onSchedulingTurn]);

  useRTVIClientEvent(RTVIEvent.ServerMessage, handleServerMessage);

  async function startCall() {
    if (!client || busy || connected) return;
    setError(null);
    try {
      await client.initDevices();
      await client.startBotAndConnect({
        endpoint: `${endpoint.replace(/\/$/, "")}/start`,
        requestData: {
          transport: "webrtc",
          body: { source: "prosper-agent-studio" },
        },
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The voice pipeline could not connect.");
    }
  }

  async function endCall() {
    setError(null);
    try {
      if (client?.connected) await client.disconnect();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The call could not disconnect cleanly.");
    }
  }

  return (
    <section className="voice-call-panel" aria-labelledby="voice-call-title">
      <header className="voice-call-header">
        <div>
          <span className="eyebrow">PRIMARY TESTING SURFACE</span>
          <h2 id="voice-call-title">Talk to Prosper Scheduler</h2>
          <p>Speak naturally. Your live transcript and the agent’s answer stay on this screen.</p>
        </div>
        <span className={`voice-status voice-status-${state}`}><i />{connectionLabel(state)}</span>
      </header>

      <div className="voice-call-body">
        <div className="voice-stage">
          {connected ? (
            <PipecatClientMicToggle>
              {({ isMicEnabled, onClick }) => (
                <button
                  className={`voice-record-button ${isMicEnabled ? "recording" : "muted"}`}
                  type="button"
                  onClick={onClick}
                  aria-label={isMicEnabled ? "Mute microphone" : "Unmute microphone"}
                >
                  {isMicEnabled ? (
                    <VoiceVisualizer participantType="local" barColor="#a7f0d4" barGap={3} barWidth={3} barMaxHeight={34} />
                  ) : (
                    <span>×</span>
                  )}
                </button>
              )}
            </PipecatClientMicToggle>
          ) : (
            <button
              className="voice-record-button"
              type="button"
              onClick={() => void startCall()}
              disabled={busy}
              aria-label="Start voice call"
            >
              <span>●</span>
            </button>
          )}
          <strong>{connected ? "Listening" : busy ? "Connecting…" : "Start talking"}</strong>
          <p>{connected ? "Pause when you finish. Tap the recording button to mute." : "Press the recording button once and allow microphone access."}</p>
        </div>

        <div className="voice-transcript" aria-live="polite">
          <div className="voice-transcript-title">
            <span>Live conversation</span>
            <small>{messages.length ? `${messages.length} message${messages.length === 1 ? "" : "s"}` : "Waiting to start"}</small>
          </div>
          <div className="voice-transcript-scroll">
            {messages.length === 0 ? (
              <div className="voice-transcript-empty">
                <span>“ ”</span>
                <p>Your transcript and the assistant’s response will appear here.</p>
              </div>
            ) : messages.map((message, index) => {
              const text = message.parts.map((part) => readablePart(part.text)).join("").trim();
              if (!text || !["user", "assistant"].includes(message.role)) return null;
              return (
                <article className={`voice-message voice-message-${message.role}`} key={`${message.createdAt}-${index}`}>
                  <span>{message.role === "user" ? "Patient" : "Assistant"}</span>
                  <p>{text}</p>
                  {!message.final && <small>Listening…</small>}
                </article>
              );
            })}
          </div>
        </div>
      </div>

      {error && <div className="voice-error" role="alert">{error}</div>}

      <footer className="voice-call-controls">
        <span>{connected ? "Live voice session" : "The call begins only after you press record."}</span>
        {connected && <button className="voice-end" type="button" onClick={() => void endCall()}>End call</button>}
      </footer>
      <PipecatClientAudio />
    </section>
  );
}

export function VoiceCallPanel({ endpoint, onSchedulingTurn }: VoiceCallPanelProps) {
  const [client] = useState(() => new PipecatClient({
    transport: new SmallWebRTCTransport(),
    enableMic: true,
    enableCam: false,
  }));

  useEffect(() => () => {
    if (client.connected) void client.disconnect();
  }, [client]);

  return (
    <PipecatClientProvider client={client}>
      <VoiceCallContent endpoint={endpoint} onSchedulingTurn={onSchedulingTurn} />
    </PipecatClientProvider>
  );
}
