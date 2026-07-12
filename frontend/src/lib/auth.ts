import { useSyncExternalStore } from "react";

/**
 * Operate-token store (review 4, NEW-3).
 *
 * After the N1 fail-closed guard, GitHub-writing endpoints require the operate token both
 * configured on the server *and* presented as `Authorization: Bearer`. The SPA had no way to send
 * one, so Re-run/Sync were dead in every deployment. This keeps the token the user pastes in the
 * settings menu — in this browser only (localStorage) — and exposes it to `api.ts` (which attaches
 * it to every request) and to the write controls (which hide themselves when no token is present).
 *
 * localStorage, not a cookie: the API is Bearer-only (no cookie auth), so there is no CSRF surface,
 * and a value that never rides automatically on cross-site requests is the safer default here.
 */

const KEY = "actionsplane.operate_token";
const listeners = new Set<() => void>();

/** The operate token saved in this browser, or "" if none. Safe in private/sandboxed contexts. */
export function getOperateToken(): string {
  try {
    return window.localStorage.getItem(KEY) ?? "";
  } catch {
    return ""; // localStorage can throw in private mode / sandboxed iframes
  }
}

/** Persist the token (or clear it when blank) and notify subscribers so the UI updates. */
export function setOperateToken(token: string): void {
  const trimmed = token.trim();
  try {
    if (trimmed) window.localStorage.setItem(KEY, trimmed);
    else window.localStorage.removeItem(KEY);
  } catch {
    /* best-effort: if storage is unavailable the token just won't persist */
  }
  for (const notify of listeners) notify();
}

function subscribe(callback: () => void): () => void {
  listeners.add(callback);
  // Cross-tab sync: saving the token in one tab updates the others (key === null on clear()).
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY || e.key === null) callback();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(callback);
    window.removeEventListener("storage", onStorage);
  };
}

/** Reactive operate token — components re-render when it is saved or cleared. */
export function useOperateToken(): string {
  return useSyncExternalStore(subscribe, getOperateToken, () => "");
}

/** Reactive "is a write credential available?", for gating the write controls. */
export function useHasOperateToken(): boolean {
  return useOperateToken().length > 0;
}
