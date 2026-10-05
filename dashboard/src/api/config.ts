// Build-time public endpoints. Credentials belong in the backend's .env.
export function normalizeBaseUrl(value: string): string {
  const base = value.trim().replace(/\/+$/, "");
  if (base === "" || (base.startsWith("/") && !base.startsWith("//") && !/[?#]/.test(base))) return base;
  const url = new URL(base);
  if (!/^https?:$/.test(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error("API base URL must be an HTTP(S) URL or a root-relative path, without credentials or query parameters");
  }
  return base;
}

export const API_BASE_URL = normalizeBaseUrl(import.meta.env?.VITE_API_BASE_URL ?? "http://127.0.0.1:8123");
export const GATEWAY_BASE_URL = normalizeBaseUrl(import.meta.env?.VITE_GATEWAY_BASE_URL ?? "http://127.0.0.1:8700");

export function isApiRequest(request: string, pageUrl: string, base = API_BASE_URL): boolean {
  const api = new URL(`${base}/`, pageUrl);
  const url = new URL(request, pageUrl);
  return url.origin === api.origin && url.pathname.startsWith(api.pathname);
}
