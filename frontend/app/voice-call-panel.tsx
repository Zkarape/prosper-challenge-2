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
  useRTVIClientEvent,
} from "@pipecat-ai/client-react";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";

type VoiceCallPanelProps = {
  endpoint: string;
  onSchedulingTurn: (turn: unknown) => void;
};

type DisplayMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
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
      return "Ready to start";
  }
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
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const connected = state === "ready" || state === "connected";
  const busy = ["authenticating", "authenticated", "connecting", "disconnecting"].includes(state);

  const handleServerMessage = useCallback((value: unknown) => {
    const message = unwrapServerMessage(value);
    if (message?.type === "scheduling_greeting") {
      const payload = message.payload as { text?: unknown } | undefined;
      if (typeof payload?.text === "string") {
        setMessages([{ id: "greeting", role: "assistant", text: payload.text }]);
      }
    } else if (message?.type === "scheduling_turn") {
      const payload = message.payload as {
        message_number?: unknown;
        patient_text?: unknown;
        assistant_message?: unknown;
      } | undefined;
      if (
        typeof payload?.message_number === "number"
        && typeof payload.patient_text === "string"
        && typeof payload.assistant_message === "string"
      ) {
        const number = payload.message_number;
        setMessages((existing) => [
          ...existing.filter((item) => !item.id.endsWith(`-${number}`)),
          { id: `patient-${number}`, role: "user", text: payload.patient_text as string },
          { id: `assistant-${number}`, role: "assistant", text: payload.assistant_message as string },
        ]);
      }
      onSchedulingTurn(message.payload);
    } else if (message?.type === "scheduling_error") {
      setError("The scheduling engine could not safely process that turn.");
    }
  }, [onSchedulingTurn]);

  useRTVIClientEvent(RTVIEvent.ServerMessage, handleServerMessage);

  async function startCall() {
    if (!client || busy || connected) return;
    setError(null);
    setMessages([]);
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
        <h2 id="voice-call-title">Prosper Scheduler</h2>
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
                <p>Start a call to see the conversation.</p>
              </div>
            ) : messages.map((message) => (
                <article className={`voice-message voice-message-${message.role}`} key={message.id}>
                  <span>{message.role === "user" ? "Patient" : "Assistant"}</span>
                  <p>{message.text}</p>
                </article>
            ))}
          </div>
        </div>
      </div>

      {error && <div className="voice-error" role="alert">{error}</div>}

      {connected && (
        <footer className="voice-call-controls">
          <button className="voice-end" type="button" onClick={() => void endCall()}>End call</button>
        </footer>
      )}
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
