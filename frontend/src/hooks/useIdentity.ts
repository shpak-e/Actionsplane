import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useOperateToken } from "../lib/auth";

/**
 * The caller's resolved identity + role (RBAC). Re-fetches when the operate token changes so the
 * UI reflects the new role immediately after pasting a token. In tokenless "open" mode the server
 * answers as a viewer; when a token is configured but none is set here, `/access/me` 401s and the
 * role is simply undefined (write controls stay gated by the backend regardless).
 */
export function useIdentity() {
  const token = useOperateToken();
  const q = useQuery({
    queryKey: ["me", token],
    queryFn: api.me,
    retry: false,
    staleTime: 30_000,
  });
  const role = q.data?.role;
  return {
    identity: q.data,
    role,
    isAdmin: role === "admin",
    canWrite: role === "operator" || role === "admin",
  };
}
