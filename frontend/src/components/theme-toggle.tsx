"use client";

import { useEffect, useMemo, useState } from "react";

type ThemeMode = "system" | "dark" | "light";

const STORAGE_KEY = "ufcml-theme";

function applyTheme(mode: ThemeMode) {
  if (typeof document === "undefined") {
    return;
  }
  if (mode === "system") {
    document.documentElement.removeAttribute("data-theme");
    return;
  }
  document.documentElement.setAttribute("data-theme", mode);
}

function readStoredTheme(): ThemeMode {
  if (typeof window === "undefined") {
    return "system";
  }
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored === "dark" || stored === "light") {
    return stored;
  }
  return "system";
}

export default function ThemeToggle() {
  const [mode, setMode] = useState<ThemeMode>(() => readStoredTheme());

  useEffect(() => {
    applyTheme(mode);
    if (typeof window !== "undefined") {
      if (mode === "system") {
        window.localStorage.removeItem(STORAGE_KEY);
      } else {
        window.localStorage.setItem(STORAGE_KEY, mode);
      }
    }
  }, [mode]);

  const label = useMemo(() => {
    if (mode === "dark") {
      return "Theme: Night";
    }
    if (mode === "light") {
      return "Theme: Day";
    }
    return "Theme: Auto";
  }, [mode]);

  function cycleMode() {
    const next: ThemeMode = mode === "system" ? "dark" : mode === "dark" ? "light" : "system";
    setMode(next);
  }

  return (
    <button type="button" className="theme-toggle" onClick={cycleMode} aria-label="Switch color theme">
      {label}
    </button>
  );
}
