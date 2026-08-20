"use client";

import { useEffect, useState } from "react";

type ThemeMode = "light" | "dark" | "system";

const choices: { value: ThemeMode; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
];

function storedTheme(): ThemeMode {
  try {
    const value = window.localStorage.getItem("prosper-theme");
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

function applyTheme(mode: ThemeMode) {
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const resolved = mode === "system" ? (prefersDark ? "dark" : "light") : mode;
  document.documentElement.dataset.themeMode = mode;
  document.documentElement.dataset.theme = resolved;
}

export function ThemeControl() {
  const [mode, setMode] = useState<ThemeMode>("system");

  useEffect(() => {
    const initialMode = storedTheme();
    const frame = window.requestAnimationFrame(() => setMode(initialMode));
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const handleSystemChange = () => {
      if ((document.documentElement.dataset.themeMode ?? "system") === "system") {
        applyTheme("system");
      }
    };

    applyTheme(initialMode);
    media.addEventListener("change", handleSystemChange);
    return () => {
      window.cancelAnimationFrame(frame);
      media.removeEventListener("change", handleSystemChange);
    };
  }, []);

  function selectTheme(nextMode: ThemeMode) {
    try {
      window.localStorage.setItem("prosper-theme", nextMode);
    } catch {
      // The selected theme still applies for this visit when storage is unavailable.
    }
    setMode(nextMode);
    applyTheme(nextMode);
  }

  return (
    <div className="theme-control" role="group" aria-label="Color theme">
      {choices.map((choice) => (
        <button
          aria-pressed={mode === choice.value}
          className={mode === choice.value ? "active" : ""}
          key={choice.value}
          onClick={() => selectTheme(choice.value)}
          type="button"
        >
          {choice.label}
        </button>
      ))}
    </div>
  );
}
