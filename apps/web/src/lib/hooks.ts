import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { api, errMsg, newKey } from "./api";
import { useSession } from "./session";
import type { Features, Me } from "./types";
import { toast } from "../components/ui";

export function useMe() {
  const token = useSession((s) => s.token);
  const orgId = useSession((s) => s.activeOrgId);
  return useQuery({ queryKey: ["me", token, orgId], queryFn: () => api<Me>("/me"), enabled: Boolean(token), staleTime: 60_000, retry: false });
}

export function useFeatures(enabled = true) {
  const { data: me } = useMe();
  return useQuery({ queryKey: ["features", me?.active_organization_id], queryFn: () => api<Features>("/features"), enabled: enabled && Boolean(me?.active_organization_id), staleTime: 30_000 });
}

/** Mutation that toasts the server's message on failure and invalidates the given query keys on success. */
export function useAction<TVars = void, TOut = unknown>(fn: (v: TVars) => Promise<TOut>, opts: { invalidate?: QueryKey[]; success?: string; onSuccess?: (o: TOut) => void } = {}) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (out) => {
      for (const k of opts.invalidate ?? []) void qc.invalidateQueries({ queryKey: k });
      if (opts.success) toast.ok(opts.success);
      opts.onSuccess?.(out);
    },
    onError: (e) => toast.bad(errMsg(e)),
  });
}
export { newKey };
