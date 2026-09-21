import { useCallback, useEffect, useState } from "react";

export type Theme = "dark" | "light";
const KEY = "onix.theme";
const LEGACY_KEY = "sentinel.theme"; // pre-rename key, read once so the preference survives

function readTheme(): Theme {
  try {
    const t = localStorage.getItem(KEY) ?? localStorage.getItem(LEGACY_KEY);
    if (t === "light" || t === "dark") return t;
  } catch {
    /* ignore */
  }
  return "dark";
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(readTheme);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    root.style.colorScheme = theme;
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  const toggle = useCallback(() => setThemeState((t) => (t === "dark" ? "light" : "dark")), []);
  const setTheme = useCallback((t: Theme) => setThemeState(t), []);
  return { theme, toggle, setTheme };
}
