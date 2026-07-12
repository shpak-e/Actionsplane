import { useState } from "react";
import { getOperateToken, setOperateToken, useHasOperateToken } from "../lib/auth";
import { IconCheck, IconKey } from "./ui";

/**
 * Header settings popover for the operate API token (review 4, NEW-3). Paste the server's
 * `ACTIONSPLANE_API_TOKEN` here to enable the write controls (Re-run, Sync); the token lives only
 * in this browser (localStorage) and is attached as `Authorization: Bearer` by `api.ts`. The key
 * button glows when a token is set. Tokenless "open" deployments simply never set one and the
 * write controls stay hidden.
 */
export function SettingsMenu() {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const hasToken = useHasOperateToken();

  function toggle() {
    if (open) {
      setOpen(false);
    } else {
      setDraft(getOperateToken());
      setOpen(true);
    }
  }

  function save() {
    setOperateToken(draft);
    setOpen(false);
  }

  function clear() {
    setOperateToken("");
    setDraft("");
    setOpen(false);
  }

  return (
    <div className="settings">
      <button
        className={`icon-btn${hasToken ? " on" : ""}`}
        onClick={toggle}
        title={
          hasToken
            ? "Operate token set — writes enabled. Click to change."
            : "Set an operate token to enable Re-run / Sync"
        }
        aria-label="API token settings"
        aria-expanded={open}
      >
        <IconKey />
      </button>

      {open && (
        <>
          <div className="settings-scrim" onClick={() => setOpen(false)} />
          <div className="settings-pop" role="dialog" aria-label="Operate API token">
            <label className="settings-label" htmlFor="operate-token">
              Operate API token
            </label>
            <p className="settings-hint">
              Sent as <code>Authorization: Bearer</code> on every request and stored only in this
              browser. Required for Re-run and Sync.
            </p>
            <input
              id="operate-token"
              type="password"
              className="settings-input"
              placeholder="paste ACTIONSPLANE_API_TOKEN"
              value={draft}
              autoComplete="off"
              autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") save();
                if (e.key === "Escape") setOpen(false);
              }}
            />
            <div className="settings-actions">
              <button className="btn sm ghost" onClick={clear} disabled={!hasToken && !draft.trim()}>
                Clear
              </button>
              <button className="btn sm primary" onClick={save} disabled={!draft.trim()}>
                <IconCheck /> Save
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
