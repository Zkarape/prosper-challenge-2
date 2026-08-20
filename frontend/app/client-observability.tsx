"use client";

import { useEffect } from "react";

type ClientObservabilityProps = {
  apiUrl: string;
};

function report(apiUrl: string, event: string, error: unknown) {
  const normalized = error instanceof Error ? error : new Error(String(error));
  void fetch(`${apiUrl}/api/system/client-events`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      level: "ERROR",
      event,
      message: normalized.message || normalized.name,
      stack: normalized.stack,
      path: window.location.pathname,
    }),
    keepalive: true,
  }).catch(() => {
    // Reporting must never cause another unhandled browser error.
  });
}

export function ClientObservability({ apiUrl }: ClientObservabilityProps) {
  useEffect(() => {
    const handleError = (event: ErrorEvent) => {
      report(apiUrl, "browser_error", event.error ?? event.message);
    };
    const handleRejection = (event: PromiseRejectionEvent) => {
      report(apiUrl, "unhandled_promise_rejection", event.reason);
    };

    window.addEventListener("error", handleError);
    window.addEventListener("unhandledrejection", handleRejection);
    return () => {
      window.removeEventListener("error", handleError);
      window.removeEventListener("unhandledrejection", handleRejection);
    };
  }, [apiUrl]);

  return null;
}
