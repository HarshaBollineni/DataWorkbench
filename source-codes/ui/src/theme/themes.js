// Plan 3 / D8.1 — colour-only palettes applied via CSS variables.
// Each palette sets a small set of *anchor* colours; derived tokens (hover,
// ring, sidebar text) are computed with color-mix() against a semantic map so
// background-to-text contrast scales automatically (WCAG-AA), rather than being
// hand-picked hex per element.

// Galileo 1.0: the Genpact palette is the brand — Sunset Orange primary on
// Midnight Black chrome. Single theme; old Minimalist/Digital/Bold retired
// (any persisted key falls back here via applyTheme).
export const THEMES = {
  genpact: { label: "Genpact (default)", dark: "#181C23", primary: "#FFAD28", chart1: "#FFAD28" },
};

export const THEME_KEYS = Object.keys(THEMES);

// Apply a palette by writing CSS custom properties on :root. Derived tokens use
// color-mix so they stay tied to the anchor colours (and remain AA-legible).
export function applyTheme(key) {
  const t = THEMES[key] || THEMES.genpact;
  const root = document.documentElement;
  root.style.setProperty("--color-dq-dark", t.dark);
  root.style.setProperty("--color-dq-purple", t.primary);
  root.style.setProperty("--color-chart-1", t.chart1);
  // Derived, contrast-aware tokens.
  root.style.setProperty("--dq-primary-hover", `color-mix(in srgb, ${t.primary}, black 14%)`);
  root.style.setProperty("--dq-primary-soft", `color-mix(in srgb, ${t.primary}, white 86%)`);
  // Sidebar text always derived toward white from the dark anchor → AA on dark.
  root.style.setProperty("--dq-sidebar-fg", `color-mix(in srgb, white 92%, ${t.dark})`);
  root.dataset.theme = key;
}
