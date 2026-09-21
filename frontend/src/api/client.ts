import type { ErrorEnvelope } from "./types";

export const API_BASE = "/api/v1";

/** A failed API call. `code` is the server's stable machine-readable code. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

/** A human-readable message for any thrown value. */
export function errorMessage(e: unknown): string {
  if (isApiError(e)) return e.message;
  if (e instanceof TypeError) return "Could not reach the server.";
  if (e instanceof Error && e.message) return e.message;
  return "Something went wrong.";
}

type UnauthenticatedHandler = () => void;
let onUnauthenticated: UnauthenticatedHandler | null = null;

/**
 * Register what happens when any call (other than login) comes back 401.
 * The auth layer uses this to drop the cached user so guards redirect to /login.
 */
export function setUnauthenticatedHandler(handler: UnauthenticatedHandler | null): void {
  onUnauthenticated = handler;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  /** When true, a 401 does not trigger the global unauthenticated handler. */
  quiet401?: boolean;
}

function isEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const err = (value as { error?: unknown }).error;
  return (
    typeof err === "object" &&
    err !== null &&
    typeof (err as { code?: unknown }).code === "string" &&
    typeof (err as { message?: unknown }).message === "string"
  );
}

/**
 * Call the API. Resolves with the parsed JSON body (or undefined for 204) and
 * rejects with an ApiError carrying the server's envelope on any non-2xx.
 */
export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, headers = {}, quiet401 = false } = options;
  const init: RequestInit = {
    method,
    credentials: "include",
    headers: { Accept: "application/json", ...headers },
  };
  if (body !== undefined) {
    init.headers = { ...init.headers, "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }

  const response = await fetch(`${API_BASE}${path}`, init);

  if (response.status === 204) {
    return undefined as T;
  }

  let payload: unknown = undefined;
  const text = await response.text();
  if (text.length > 0) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = undefined;
    }
  }

  if (!response.ok) {
    if (response.status === 401 && !quiet401) {
      onUnauthenticated?.();
    }
    if (isEnvelope(payload)) {
      const { code, message, details } = payload.error;
      throw new ApiError(response.status, code, message, details);
    }
    throw new ApiError(
      response.status,
      "http_error",
      `Request failed with status ${response.status}.`,
    );
  }

  return payload as T;
}

/**
 * A fresh key per submit attempt. Mutating endpoints a client may retry accept
 * an Idempotency-Key header; a retry with the same key replays the response.
 */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // RFC 4122 v4 from getRandomValues, for older browsers.
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
