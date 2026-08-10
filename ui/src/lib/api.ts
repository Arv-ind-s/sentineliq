export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export function apiPath(path: string): string {
  if (path === "/api" || path.startsWith("/api/")) return path;
  return path.startsWith("/") ? `/api${path}` : `/api/${path}`;
}

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiPath(path), {
    ...init,
    headers: { Accept: "application/json", ...init?.headers },
    credentials: init?.credentials ?? "same-origin",
  });
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // The status remains the useful diagnostic when a proxy returns non-JSON.
    }
    const detail = body && typeof body === "object" && "error" in body && body.error && typeof body.error === "object" && "message" in body.error && typeof body.error.message === "string" ? body.error.message : `Request failed (${response.status})`;
    throw new ApiError(response.status, detail, body);
  }
  return response.json() as Promise<T>;
}
