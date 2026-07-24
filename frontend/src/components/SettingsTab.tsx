import { useState } from "react";
import { ReposTab } from "./ReposTab";
import { NotificationsPanel } from "./NotificationsPanel";
import { IconKey, IconShield } from "./ui";

type Section = "notifications" | "repositories" | "access" | "users";

const SECTIONS: { id: Section; label: string; hint: string }[] = [
  { id: "notifications", label: "Notifications", hint: "Slack, webhook & email alerts" },
  { id: "repositories", label: "Repositories", hint: "Repos ActionsPlane watches" },
  { id: "access", label: "Access (OIDC)", hint: "Single sign-on" },
  { id: "users", label: "Users", hint: "Team members & roles" },
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
          {section === "access" && (
            <Placeholder icon={<IconShield size={30} />} title="Single sign-on (OIDC)">
              <p>
                Today ActionsPlane authenticates writes with a single <strong>operate token</strong>{" "}
                (set via the key icon, top-right). OIDC will let your team sign in with your identity
                provider (Okta, Google Workspace, Entra ID, GitHub) instead of sharing a token.
              </p>
              <p className="subtle">
                Planned configuration: issuer URL, client ID/secret, allowed domains, and a
                group→role mapping. Track this in the roadmap under Access.
              </p>
            </Placeholder>
          )}
          {section === "users" && (
            <Placeholder icon={<IconKey size={30} />} title="Users & roles">
              <p>
                Once single sign-on is enabled, invited members will appear here with roles —{" "}
                <strong>viewer</strong> (read the dashboard), <strong>operator</strong> (open fix
                PRs, run campaigns), and <strong>admin</strong> (manage settings and access).
              </p>
              <p className="subtle">
                Until then, anyone with the operate token has operator access, and the read token (if
                configured) grants view-only.
              </p>
            </Placeholder>
          )}
        </div>
      </div>
    </section>
  );
}

export type { Section as SettingsSection };
