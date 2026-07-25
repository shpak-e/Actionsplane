import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { promptForToken } from "../lib/auth";
import { useIdentity } from "../hooks/useIdentity";
import { EmptyState, ErrorBanner, IconCheck, IconKey, IconX, TableSkeleton } from "./ui";
import type { ApiToken } from "../types";

const ROLES: { id: string; label: string; hint: string }[] = [
  { id: "viewer", label: "Viewer", hint: "read-only — dashboards & audit, no writes" },
  { id: "operator", label: "Operator", hint: "run campaigns, apply fixes, manage notifications" },
  { id: "admin", label: "Admin", hint: "everything, incl. managing these tokens" },
];
const ROLE_BADGE: Record<string, string> = {
  admin: "running",
  operator: "ok",
  viewer: "neutral",
};

function TokenRow({ token }: { token: ApiToken }) {
  const qc = useQueryClient();
  const revoke = useMutation({
    mutationFn: () => api.deleteApiToken(token.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-tokens"] }),
  });
  return (
    <div className="card channel-row">
      <div className="channel-main">
        <div className="channel-head">
          <IconKey size={15} />
          <span className="channel-name">{token.name}</span>
          <span className={`badge ${ROLE_BADGE[token.role] ?? "neutral"}`}>{token.role}</span>
        </div>
        <div className="channel-dest mono subtle">
          {token.prefix}…{token.created_by ? ` · created by ${token.created_by}` : ""}
        </div>
        {revoke.isError && (
          <div className="toast err" style={{ marginTop: 8 }}>
            <IconX /> {(revoke.error as Error).message}
          </div>
        )}
      </div>
      <div className="channel-actions">
        <button
          className="btn sm danger"
          onClick={() => revoke.mutate()}
          disabled={revoke.isPending}
          title="Revoke this token"
        >
          {revoke.isPending ? "Revoking…" : "Revoke"}
        </button>
      </div>
    </div>
  );
}

export function TokensPanel() {
  const { role, isAdmin } = useIdentity();
  const qc = useQueryClient();
  const tokens = useQuery({
    queryKey: ["api-tokens"],
    queryFn: api.apiTokens,
    enabled: isAdmin,
  });

  const [name, setName] = useState("");
  const [newRole, setNewRole] = useState("operator");
  const [secret, setSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const create = useMutation({
    mutationFn: () => api.createApiToken({ name: name.trim(), role: newRole }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["api-tokens"] });
      setSecret(res.secret);
      setCopied(false);
      setName("");
    },
  });

  if (!isAdmin) {
    return (
      <div className="card settings-placeholder">
        <div className="settings-placeholder-icon">
          <IconKey size={30} />
        </div>
        <h3>API access &amp; roles</h3>
        <div className="settings-placeholder-body">
          <p>
            Named API tokens carry a role — <strong>viewer</strong> (read-only),{" "}
            <strong>operator</strong> (run campaigns &amp; apply fixes), and{" "}
            <strong>admin</strong> (manage tokens). Managing tokens requires the{" "}
            <strong>admin</strong> role.
          </p>
          <p className="subtle">
            You are currently <strong>{role ?? "unauthenticated"}</strong>.{" "}
            <button className="link-cell" onClick={promptForToken}>
              Set an admin token
            </button>{" "}
            to manage access.
          </p>
        </div>
      </div>
    );
  }

  const list = tokens.data ?? [];

  return (
    <div>
      <p className="settings-hint" style={{ marginBottom: 16 }}>
        Issue named API tokens, each with a role. The legacy env tokens still work
        (<span className="mono">ACTIONSPLANE_API_TOKEN</span> = admin,{" "}
        <span className="mono">ACTIONSPLANE_API_READ_TOKEN</span> = viewer). A token's secret is
        shown once, on creation — only its hash is stored.
      </p>

      <div className="card token-create">
        <div className="token-create-row">
          <input
            className="input"
            placeholder="Token name (e.g. deploy-bot)"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <select className="input" value={newRole} onChange={(e) => setNewRole(e.target.value)}>
            {ROLES.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>
          <button
            className="btn sm primary"
            disabled={!name.trim() || create.isPending}
            onClick={() => create.mutate()}
          >
            {create.isPending ? "Creating…" : "Create token"}
          </button>
        </div>
        <div className="subtle" style={{ fontSize: 11.5, marginTop: 6 }}>
          {ROLES.find((r) => r.id === newRole)?.hint}
        </div>
        {create.isError && (
          <div className="toast err" style={{ marginTop: 10 }}>
            <IconX /> {(create.error as Error).message}
          </div>
        )}
        {secret && (
          <div className="token-secret" style={{ marginTop: 10 }}>
            <div className="token-secret-head">
              <IconCheck /> Copy this token now — it won't be shown again.
            </div>
            <div className="token-secret-value">
              <code className="mono">{secret}</code>
              <button
                className="btn sm"
                onClick={() => {
                  navigator.clipboard?.writeText(secret);
                  setCopied(true);
                }}
              >
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
          </div>
        )}
      </div>

      {tokens.isError ? (
        <ErrorBanner error={tokens.error} />
      ) : tokens.isLoading ? (
        <TableSkeleton rows={3} />
      ) : list.length === 0 ? (
        <EmptyState icon={<IconKey size={30} />} title="No API tokens yet">
          Create one above to grant scoped, revocable access without sharing the env token.
        </EmptyState>
      ) : (
        <div className="token-list">
          {list.map((t) => (
            <TokenRow key={t.id} token={t} />
          ))}
        </div>
      )}
    </div>
  );
}
