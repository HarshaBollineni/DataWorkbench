import { useCallback, useEffect, useState } from "react";

import { applyTheme, THEME_KEYS } from "./themes";
import { updateProfile } from "@/api/client";

const KEY = "dq_theme";

// Old persisted keys (minimalist/digital/bold) predate the Genpact-only palette;
// normalize anything unknown to the first (and only) valid theme.
function normalize(key) {
  return THEME_KEYS.includes(key) ? key : THEME_KEYS[0];
}

// useTheme — applies the palette via CSS vars, persists to localStorage (instant
// re-apply on reload) and to the user's profile (server-side).
export function useTheme(initial) {
  const [theme, setThemeState] = useState(
    () => normalize(localStorage.getItem(KEY) || initial)
  );

  useEffect(() => { applyTheme(theme); }, [theme]);

  const setTheme = useCallback((key) => {
    if (!THEME_KEYS.includes(key)) return;
    setThemeState(key);
    localStorage.setItem(KEY, key);
    applyTheme(key);
    updateProfile({ theme: key }).catch(() => {});
  }, []);

  return { theme, setTheme };
}

// Apply the persisted theme as early as possible (called from main/App).
export function bootstrapTheme(userTheme) {
  const key = normalize(localStorage.getItem(KEY) || userTheme);
  applyTheme(key);
  localStorage.setItem(KEY, key);
}
