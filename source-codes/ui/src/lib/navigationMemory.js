const STORAGE_PREFIX = "archimedes.navigation-memory";

export const NAVIGATION_SECTIONS = [
  { root: "/", matches: (path) => path === "/" },
  { root: "/data-sourcing", matches: (path) => path === "/data-sourcing" },
  { root: "/test-lab", matches: (path) => path === "/test-lab" || path.startsWith("/test-lab/") },
  { root: "/issues", matches: (path) => path === "/issues" || path.startsWith("/issues/") },
  { root: "/knowledge-base", matches: (path) => path === "/knowledge-base" },
  { root: "/dq-framework", matches: (path) => path === "/dq-framework" },
  { root: "/admin", matches: (path) => path === "/admin" },
  { root: "/profile", matches: (path) => path === "/profile" },
];

function userScope(user) {
  const tenant = user?.tenant_id || user?.tenant || "default";
  const identity = user?.user_id || user?.id || user?.username || user?.email || user?.name || "authenticated";
  return `${STORAGE_PREFIX}:${tenant}:${identity}`;
}

export function navigationSection(pathname) {
  return NAVIGATION_SECTIONS.find((section) => section.matches(pathname))?.root || null;
}

function readMemory(user) {
  try {
    return JSON.parse(sessionStorage.getItem(userScope(user)) || "{}") || {};
  } catch {
    return {};
  }
}

export function rememberSectionLocation(user, location) {
  const section = navigationSection(location.pathname);
  if (!section) return;
  const destination = `${location.pathname}${location.search}${location.hash}`;
  try {
    const memory = readMemory(user);
    memory[section] = destination;
    sessionStorage.setItem(userScope(user), JSON.stringify(memory));
  } catch {
    // Navigation still works when storage is unavailable.
  }
}

export function rememberedSectionLocation(user, root) {
  const destination = readMemory(user)[root];
  if (typeof destination !== "string" || !destination.startsWith("/")) return root;
  return navigationSection(destination.split(/[?#]/, 1)[0]) === root ? destination : root;
}

export function clearNavigationMemory(user) {
  try { sessionStorage.removeItem(userScope(user)); } catch { /* storage unavailable */ }
}
