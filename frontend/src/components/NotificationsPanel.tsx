import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useHasOperateToken, promptForToken } from "../lib/auth";
import { ChannelLogo, EmptyState, ErrorBanner, IconCheck, IconX, TableSkeleton } from "./ui";
import type { NotificationChannel } from "../types";

const KIND_LABEL: Record<string, string> = { slack: "Slack", webhook: "Webhook", email: "Email" };
const KINDS: { id: string; label: string }[] = [
  { id: "slack", label: "Slack" },
  { id: "webhook", label: "Webhook" },
  { id: "email", label: "Email" },
];
const DEST_FIELD: Record<string, { key: "url" | "to"; label: string; placeholder: string }> = {
  slack: { key: "url", label: "Slack Incoming Webhook URL", placeholder: "https://hooks.slack.com/services/…" },
  webhook: { key: "url", label: "Webhook URL", placeholder: "https://example.com/hooks/actionsplane" },
  email: { key: "to", label: "Recipient email", placeholder: "alerts@example.com" },
};

function ChannelRow({ channel, eventLabels }: { channel: NotificationChannel; eventLabels: Map<string, string> }) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["notification-channels"] });

  const toggle = useMutation({
    mutationFn: () => api.updateChannel(channel.id, { enabled: !channel.enabled }),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => api.deleteChannel(channel.id),
    onSuccess: invalidate,
  });
  const test = useMutation({ mutationFn: () => api.testChannel(channel.id) });

  return (
    <div className={channel.enabled ? "card channel-row" : "card channel-row off"}>
      <div className="channel-main">
        <div className="channel-head">
          <ChannelLogo kind={channel.kind} size={17} />
          <span className="channel-name">{channel.name}</span>
          <span className="channel-kind">{KIND_LABEL[channel.kind] ?? channel.kind}</span>
          {!channel.enabled && <span className="badge neutral">disabled</span>}
        </div>
        <div className="channel-dest mono subtle">{channel.target_hint ?? "—"}</div>
        <div className="channel-events">
          {channel.events.length === 0 ? (
            <span className="subtle" style={{ fontSize: 11.5 }}>No events subscribed</span>
          ) : (
            channel.events.map((e) => (
              <span key={e} className="event-chip">
                {eventLabels.get(e) ?? e}
              </span>
            ))
          )}
        </div>
        {test.data && (
          <div className={`toast ${test.data.status === "sent" ? "ok" : "err"}`} style={{ marginTop: 8 }}>
            {test.data.status === "sent" ? <IconCheck /> : <IconX />}{" "}
            {test.data.status === "sent" ? "Test alert delivered." : test.data.detail}
          </div>
        )}
        {test.isError && (
          <div className="toast err" style={{ marginTop: 8 }}>
            <IconX /> {(test.error as Error).message}
          </div>
        )}
      </div>
      <div className="channel-actions">
        <button className="btn sm" onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? "Sending…" : "Send test"}
        </button>
        <button className="btn sm" onClick={() => toggle.mutate()} disabled={toggle.isPending}>
          {channel.enabled ? "Disable" : "Enable"}
        </button>
        <button
          className="btn sm danger"
          onClick={() => remove.mutate()}
          disabled={remove.isPending}
          title="Delete channel"
        >
          Remove
        </button>
      </div>
    </div>
  );
}

export function NotificationsPanel() {
  const hasToken = useHasOperateToken();
  const qc = useQueryClient();
  const channels = useQuery({ queryKey: ["notification-channels"], queryFn: api.notificationChannels });
  const events = useQuery({ queryKey: ["notification-events"], queryFn: api.notificationEvents });
  const eventLabels = new Map((events.data ?? []).map((e) => [e.type, e.label]));

  const [kind, setKind] = useState("slack");
  const [name, setName] = useState("");
  const [dest, setDest] = useState("");
  const [selEvents, setSelEvents] = useState<string[]>([]);

  const create = useMutation({
    mutationFn: () => {
      const field = DEST_FIELD[kind];
      return api.createChannel({ kind, name, config: { [field.key]: dest }, events: selEvents });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notification-channels"] });
      setName("");
      setDest("");
      setSelEvents([]);
    },
  });

  const field = DEST_FIELD[kind];
  const canCreate = hasToken && name.trim() && dest.trim();

  return (
    <div>
      <p className="settings-hint" style={{ marginBottom: 16 }}>
        Route fleet events to Slack, a generic webhook, or email. Alerts fire once per event
        (de-duplicated), and delivery never blocks an audit or a campaign.
        {!hasToken && (
          <>
            {" "}
            <button className="link-cell" onClick={promptForToken}>
              Set an operate token
            </button>{" "}
            to manage channels.
          </>
        )}
      </p>

      <div className="card notif-form">
        <div className="notif-kind-picker">
          {KINDS.map((k) => (
            <button
              key={k.id}
              type="button"
              className={kind === k.id ? "notif-kind active" : "notif-kind"}
              onClick={() => setKind(k.id)}
            >
              <ChannelLogo kind={k.id} size={22} />
              <span>{k.label}</span>
            </button>
          ))}
        </div>

        <div className="notif-form-grid">
          <label className="ffield">
            <span className="ffield-label">Name</span>
            <input
              className="settings-input"
              value={name}
              placeholder="e.g. #platform-alerts"
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="ffield">
            <span className="ffield-label">{field.label}</span>
            <input
              className="settings-input"
              value={dest}
              placeholder={field.placeholder}
              onChange={(e) => setDest(e.target.value)}
            />
          </label>
        </div>

        <div className="notif-events">
          <span className="settings-label">Fire on</span>
          <div className="notif-events-grid">
            {(events.data ?? []).map((ev) => (
              <label key={ev.type} className="event-toggle" title={ev.description}>
                <input
                  type="checkbox"
                  checked={selEvents.includes(ev.type)}
                  onChange={(e) =>
                    setSelEvents((s) => (e.target.checked ? [...s, ev.type] : s.filter((x) => x !== ev.type)))
                  }
                />
                {ev.label}
              </label>
            ))}
          </div>
        </div>

        {create.isError && (
          <div className="toast err">
            <IconX /> {(create.error as Error).message}
          </div>
        )}
        <div className="notif-form-actions">
          <button
            className="btn primary"
            onClick={() => (hasToken ? create.mutate() : promptForToken())}
            disabled={hasToken ? !canCreate || create.isPending : false}
            title={hasToken ? "" : "Set an operate token first"}
          >
            {create.isPending ? "Adding…" : "Add channel"}
          </button>
        </div>
      </div>

      <div className="settings-label" style={{ margin: "20px 0 10px" }}>
        Channels {channels.data ? `· ${channels.data.length}` : ""}
      </div>
      {channels.isError ? (
        <ErrorBanner error={channels.error} />
      ) : channels.isLoading ? (
        <TableSkeleton rows={3} />
      ) : (channels.data ?? []).length === 0 ? (
        <EmptyState icon={<IconCheck size={30} />} title="No channels yet">
          Add a Slack, webhook, or email channel above to start receiving alerts.
        </EmptyState>
      ) : (
        <div className="channel-list">
          {(channels.data ?? []).map((c) => (
            <ChannelRow key={c.id} channel={c} eventLabels={eventLabels} />
          ))}
        </div>
      )}
    </div>
  );
}
