import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { api, isApiError, newIdempotencyKey } from "./client";
import type {
  ApiToken,
  ApiTokenCreated,
  Health,
  ListResponse,
  Role,
  User,
} from "./types";

export const queryKeys = {
  me: ["auth", "me"] as const,
  users: ["users"] as const,
  tokens: ["api-tokens"] as const,
  health: ["health"] as const,
};

// --- auth -------------------------------------------------------------------

/** The signed-in user, or null when there is no valid session. */
export async function fetchMe(): Promise<User | null> {
  try {
    return await api<User>("/auth/me", { quiet401: true });
  } catch (e) {
    if (isApiError(e) && e.status === 401) return null;
    throw e;
  }
}

export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: fetchMe,
    staleTime: 60_000,
    // No automatic retry on this one. It is the first request of every session,
    // and RequireAuth already renders a Try again button for it, so a second
    // attempt plus its backoff only doubles the time before the guard can say
    // anything true: measured against a stopped API, 3.1s becomes 6.2s plus
    // backoff (issue #25). Every other query keeps the client's default.
    retry: false,
  });
}

export function setMe(client: QueryClient, user: User | null): void {
  client.setQueryData<User | null>(queryKeys.me, user);
}

/**
 * Replace the signed-in user and forget everything else that was cached under
 * the previous session. The user is written first so that mounted guards see
 * the change; clearing the whole cache would detach their observers instead.
 */
export function resetSession(client: QueryClient, user: User | null): void {
  setMe(client, user);
  client.removeQueries({ predicate: (query) => query.queryKey[0] !== "auth" });
}

export interface LoginInput {
  email: string;
  password: string;
}

export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: LoginInput) =>
      api<{ user: User }>("/auth/login", { method: "POST", body: input, quiet401: true }),
    onSuccess: ({ user }) => resetSession(client, user),
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<void>("/auth/logout", { method: "POST", quiet401: true }),
    onSettled: () => resetSession(client, null),
  });
}

export function useLogoutEverywhere() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<void>("/auth/logout-all", { method: "POST", quiet401: true }),
    onSettled: () => resetSession(client, null),
  });
}

// --- users ------------------------------------------------------------------

export function useUsers(enabled = true) {
  return useQuery({
    queryKey: queryKeys.users,
    queryFn: () => api<ListResponse<User>>("/users"),
    select: (data) => data.items,
    enabled,
  });
}

export interface CreateUserInput {
  email: string;
  display_name: string;
  password: string;
  role?: Role;
}

export function useCreateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateUserInput) =>
      api<User>("/users", {
        method: "POST",
        body: input,
        headers: { "Idempotency-Key": newIdempotencyKey() },
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.users }),
  });
}

export interface EditUserInput {
  email?: string;
  display_name?: string;
  role?: "admin" | "member";
  active?: boolean;
}

/** An admin's change to a member. Deactivating ends their sessions and tokens at once. */
export function useEditUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: EditUserInput }) => api<User>(`/users/${encodeURIComponent(id)}`, { method: "PATCH", body: input }),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.users }),
  });
}

/** An admin sets a new password for another member, signing them out everywhere. */
export function useSetUserPassword() {
  return useMutation({
    mutationFn: ({ id, password }: { id: string; password: string }) => api<User>(`/users/${encodeURIComponent(id)}/password`, { method: "POST", body: { password } }),
  });
}

/** Change your own password; this session stays, every other one ends. */
export function useChangePassword() {
  return useMutation({
    mutationFn: (input: { current_password: string; new_password: string }) => api<void>("/auth/password", { method: "POST", body: input }),
  });
}

// --- api tokens -------------------------------------------------------------

export function useTokens() {
  return useQuery({
    queryKey: queryKeys.tokens,
    queryFn: () => api<ListResponse<ApiToken>>("/api-tokens"),
    select: (data) => data.items,
  });
}

export function useCreateToken() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string }) =>
      api<ApiTokenCreated>("/api-tokens", {
        method: "POST",
        body: input,
        headers: { "Idempotency-Key": newIdempotencyKey() },
      }),
    // The plaintext is returned to the caller only; it is never cached.
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.tokens }),
  });
}

export function useRevokeToken() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<ApiToken>(`/api-tokens/${encodeURIComponent(id)}/revoke`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.tokens }),
  });
}

// --- health -----------------------------------------------------------------

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    // A failing check answers 503 with the same body, and the navigation still
    // needs its `features` then (D11), so that body is data, not an error.
    queryFn: () => api<Health>("/health", { acceptStatuses: [503] }),
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  });
}
