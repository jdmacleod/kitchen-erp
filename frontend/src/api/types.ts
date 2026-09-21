// Wire types for the Phase 1A API. Timestamps are ISO-8601 UTC strings and are
// localized only at render time. Decimals (none in 1A) will be strings too.

export type Role = "admin" | "member";

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  active: boolean;
  created_at: string;
}

export interface ApiToken {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface ApiTokenCreated {
  token: ApiToken;
  plaintext: string;
}

export type HealthStatus = "ok" | "degraded" | "failed";

export interface Health {
  status: HealthStatus;
  // Per-check details arrive only when authenticated; their shape is the
  // backend's to define and the shell only shows the overall word.
  [key: string]: unknown;
}

export interface ListResponse<T> {
  items: T[];
}

export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}
