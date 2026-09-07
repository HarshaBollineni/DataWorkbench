// Shared transport concerns for every backend API module. Endpoint-specific
// paths and response handling stay in client.js; authentication and JSON
// request behavior live here so future API modules do not reimplement them.
export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8001/api";

const TOKEN_KEY = "dq_token";
export const SESSION_EXPIRED_EVENT = "dq:session-expired";

export const getToken = () => localStorage.getItem(TOKEN_KEY);

export const setToken = (token) =>
  token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY);

export function authHeaders(headers = {}) {
  const token = getToken();
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...headers,
  };
}

export async function readErrorDetail(response) {
  let detail = response.statusText;
  try {
    detail = (await response.json()).detail || detail;
  } catch {
    // Preserve the HTTP status text when the error response is not JSON.
  }
  return detail;
}

export async function request(path, options = {}) {
  const { headers = {}, ...requestOptions } = options;
  const response = await fetch(`${API_BASE}${path}`, {
    headers: authHeaders({ "Content-Type": "application/json", ...headers }),
    ...requestOptions,
  });
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    // A session may expire while the SPA is open. Remove the stale credential
    // and notify AuthProvider so protected pages return to sign-in instead of
    // rendering a raw 401 inside one section of the page.
    if (response.status === 401 && getToken()) {
      setToken(null);
      window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
    }
    const message = typeof detail === "string" ? detail : detail?.message || response.statusText;
    const error = new Error(`${response.status}: ${message}`);
    error.status = response.status;
    error.detail = detail;
    throw error;
  }
  return response.json();
}
