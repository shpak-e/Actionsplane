import type { ReactNode } from "react";
import { IconMoon, IconSun } from "./ui";
import type { Theme } from "../hooks/useTheme";

export type RailTab<T extends string> = {
  id: T;
  label: string;
  icon: ReactNode;
  count?: number;
};

/** The fast-forward run-fleet mark: three run triangles, the lead one in the accent color. */
function Mark() {
  return (
    <svg viewBox="0 0 32 32" width="30" height="30" aria-label="ActionsPlane">
      <rect
        x="0.75"
        y="0.75"
        width="30.5"
        height="30.5"
        rx="7.25"
        fill="var(--surface-2)"
        stroke="var(--border-strong)"
        strokeWidth="1.5"
      />
      <path d="M8 9.5 L8 22.5 L14 16 Z" fill="var(--fg-faint)" />
      <path d="M13 9.5 L13 22.5 L19 16 Z" fill="var(--fg-muted)" />
      <path d="M18 9.5 L18 22.5 L24 16 Z" fill="var(--accent)" />
    </svg>
  );
}

export function NavRail<T extends string>({
  tabs,
  active,
  onSelect,
  theme,
  onToggleTheme,
}: {
  tabs: RailTab<T>[];
  active: T;
  onSelect: (id: T) => void;
  theme: Theme;
  onToggleTheme: () => void;
}) {
  return (
    <aside className="rail" aria-label="Sections">
      <div className="rail-mark">
        <Mark />
      </div>
      <nav className="rail-nav">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={active === t.id ? "rail-btn active" : "rail-btn"}
            onClick={() => onSelect(t.id)}
            aria-current={active === t.id}
            title={t.label}
          >
            {t.icon}
            <span className="rail-label">{t.label}</span>
            {t.count != null && t.count > 0 && <span className="rail-count">{t.count}</span>}
          </button>
        ))}
      </nav>

      <div className="rail-spacer" />

      <button
        className="rail-icon-btn"
        onClick={onToggleTheme}
        title={theme === "dark" ? "Switch to light (Blueprint Cobalt)" : "Switch to dark (Console)"}
        aria-label="Toggle theme"
      >
        {theme === "dark" ? <IconSun /> : <IconMoon />}
      </button>
    </aside>
  );
}
