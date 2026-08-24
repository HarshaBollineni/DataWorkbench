import process from "node:process";

export const E2E_BACKEND_PORT = process.env.E2E_BACKEND_PORT || "8001";
export const E2E_API_ORIGIN = `http://127.0.0.1:${E2E_BACKEND_PORT}`;
export const E2E_API_BASE = `${E2E_API_ORIGIN}/api`;
