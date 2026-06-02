import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

/** Whether the backend is in offline mode (Sync button) or App mode (live SSE updates). */
export function useMode() {
  return useQuery({ queryKey: ["mode"], queryFn: api.mode, staleTime: 60_000 });
}
