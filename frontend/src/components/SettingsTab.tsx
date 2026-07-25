import { useState } from "react";
import { ReposTab } from "./ReposTab";
import { NotificationsPanel } from "./NotificationsPanel";
import { TokensPanel } from "./TokensPanel";
import { IconShield } from "./ui";

type Section = "notifications" | "repositories" | "access" | "users";

const SECTIONS: { id: Section; label: string; hint: string }[] = [
  { id: "notifications", label: "Notifications", hint: "Slack, webhook & email alerts" },
  { id: "repositories", label: "Repositories", hint: "Repos ActionsPlane watches" },
  { id: "users", label: "API access", hint: "Tokens & roles" },
  { id: "access", label: "Single sign-on", hint: "OIDC (coming soon)" },
];

/** A not-yet-wired settings section — honest about what it will configure once built. */
function Placeholder({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="card settings-placeholder">
      <div className="settings-placeholder-icon">{icon}</div>
      <h3>{title}</h3>
      <span className="badge neutral" style={{ margin: "2px 0 10px" }}>
        Not configured yet
      </span>
      <div className="settings-placeholder-body">{children}</div>
    </div>
  );
}

export function SettingsTab({ initialSection = "notifications" }: { initialSection?: Section }) {
  const [section, setSection] = useState<Section>(initialSection);

  return (
    <section>
      <div className="content-head">
        <div>
          <h2>Settings</h2>
          <div className="sub">Notifications, watched repositories, and access control.</div>
        </div>
      </div>

      <div className="settings-layout">
        <nav className="settings-nav">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              className={section === s.id ? "settings-nav-item active" : "settings-nav-item"}
              onClick={() => setSection(s.id)}
            >
              <span className="settings-nav-label">{s.label}</span>
              <span className="settings-nav-hint">{s.hint}</span>
            </button>
          ))}
        </nav>

        <div className="settings-body">
          {section === "notifications" && <NotificationsPanel />}
          {section === "repositories" && <ReposTab />}
          {section === "users" && <TokensPanel />}
          {section === "access" && (
            <Placeholder icon={<IconShield size={30} />} title="Single sign-on (OIDC)">
              <p>
                Access is managed today with <strong>role-based API tokens</strong> (see{" "}
                <strong>API access</strong>). OIDC will additionally let your team sign in with your
                identity provider (Okta, Google Workspace, Entra ID, GitHub) instead of holding a
                token.
              </p>
              <p className="subtle">
                Planned configuration: issuer URL, client ID/secret, allowed domains, and a
                group→role mapping onto the same viewer/operator/admin roles.
              </p>
            </Placeholder>
          )}
        </div>
      </div>
    </section>
  );
}

export type { Section as SettingsSection };
