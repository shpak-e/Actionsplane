import type { ReactNode } from "react";
import type { Run } from "../types";

/* ---------- inline icons (stroke = currentColor) ---------- */

// Console (Theme 01) icon set — Feather geometry: 24-unit grid, 2px stroke, round caps/joins,
// lifted straight from the design mock so the app and the brand studies share one line language.
type IconProps = { size?: number; className?: string };
const svg = (size: number, className: string | undefined, children: ReactNode) => (
  <svg
    className={`gi ${className ?? ""}`}
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    {children}
  </svg>
);

export const IconCheck = ({ size = 14, className }: IconProps) =>
  svg(size, className, <path d="M20 6 9 17l-5-5" />);
export const IconX = ({ size = 14, className }: IconProps) =>
  svg(size, className, <path d="M18 6 6 18M6 6l12 12" />);
export const IconDots = ({ size = 14, className }: IconProps) =>
  svg(size, className, <path d="M21 12a9 9 0 1 1-6.219-8.56" />);
export const IconClock = ({ size = 14, className }: IconProps) =>
  svg(size, className, <><circle cx="12" cy="12" r="10" /><path d="M12 6v6l4 2" /></>);
export const IconRepo = ({ size = 14, className }: IconProps) =>
  svg(size, className, <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V2H6.5A2.5 2.5 0 0 0 4 4.5v15zM4 19.5A2.5 2.5 0 0 0 6.5 22H20" />);
export const IconExternal = ({ size = 13, className }: IconProps) =>
  svg(size, className, <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3" />);
export const IconRefresh = ({ size = 13, className }: IconProps) =>
  svg(size, className, <><path d="M23 4v6h-6M1 20v-6h6" /><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" /></>);
export const IconLogs = ({ size = 13, className }: IconProps) =>
  svg(size, className, <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8" /></>);
export const IconShield = ({ size = 14, className }: IconProps) =>
  svg(size, className, <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />);
export const IconActivity = ({ size = 14, className }: IconProps) =>
  svg(size, className, <path d="M22 12h-4l-3 9L9 3l-3 9H2" />);
export const IconLayers = ({ size = 14, className }: IconProps) =>
  svg(size, className, <path d="M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />);
export const IconBranch = ({ size = 13, className }: IconProps) =>
  svg(size, className, <path d="M6 3v12M6 15a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM18 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM18 9a9 9 0 0 1-9 9" />);
export const IconGraph = ({ size = 14, className }: IconProps) =>
  svg(size, className, <><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><path d="M8.59 13.51 15.42 17.49M15.41 6.51 8.59 10.49" /></>);
export const IconArrowRight = ({ size = 14, className }: IconProps) =>
  svg(size, className, <path d="M5 12h14M12 5l7 7-7 7" />);
export const IconKey = ({ size = 14, className }: IconProps) =>
  svg(size, className, <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />);
export const IconChevron = ({ size = 12, className }: IconProps) =>
  svg(size, className, <path d="M9 18l6-6-6-6" />);
export const IconWorkflow = ({ size = 13, className }: IconProps) =>
  svg(size, className, <><circle cx="12" cy="12" r="4" /><path d="M1.05 12H7M17 12h5.95" /></>);
export const IconSun = ({ size = 15, className }: IconProps) =>
  svg(size, className, <><circle cx="12" cy="12" r="5" /><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" /></>);
export const IconMoon = ({ size = 15, className }: IconProps) =>
  svg(size, className, <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />);

/* ---------- brand toolbar icons ----------
   Bespoke geometric glyphs in the logo-studies language: grey geometry + a single ember accent
   element (var(--accent), fixed so it stays ember even when the tab is idle). The rail colors
   `currentColor` grey when idle and ember when active, so an active tab glows whole. */
const brand = (size: number, className: string | undefined, children: ReactNode) => (
  <svg
    className={`gi ${className ?? ""}`}
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    {children}
  </svg>
);

// Runs — the fast-forward run fleet (1A): three run triangles, the lead one live in ember.
export const BrandRuns = ({ size = 20, className }: IconProps) =>
  brand(
    size,
    className,
    <>
      <path d="M5 6.5 L5 17.5 L10 12 Z" fill="var(--fg-faint)" />
      <path d="M9.5 6.5 L9.5 17.5 L14.5 12 Z" fill="currentColor" />
      <path d="M14 6.5 L14 17.5 L19 12 Z" fill="var(--accent)" />
    </>,
  );

// Security — shield with an ember core (the guarded target).
export const BrandSecurity = ({ size = 20, className }: IconProps) =>
  brand(
    size,
    className,
    <>
      <path
        d="M12 3 L19 5.8 V11 C19 15.4 16.1 18.4 12 20 C7.9 18.4 5 15.4 5 11 V5.8 Z"
        stroke="currentColor"
        strokeWidth="2"
      />
      <circle cx="12" cy="11" r="2.1" fill="var(--accent)" />
    </>,
  );

// Drift — stacked canonical planes, the top (current) plane lifted in ember.
export const BrandDrift = ({ size = 20, className }: IconProps) =>
  brand(
    size,
    className,
    <>
      <path d="M3 16 L12 21 L21 16" stroke="currentColor" strokeWidth="2" />
      <path d="M3 12 L12 17 L21 12" stroke="currentColor" strokeWidth="2" />
      <path d="M12 3 L21 8 L12 13 L3 8 Z" stroke="var(--accent)" strokeWidth="2" />
    </>,
  );

// Repos — the repository book with an ember node (the fleet, one repo lit).
export const BrandRepos = ({ size = 20, className }: IconProps) =>
  brand(
    size,
    className,
    <>
      <path
        d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V2H6.5A2.5 2.5 0 0 0 4 4.5v15zM4 19.5A2.5 2.5 0 0 0 6.5 22H20"
        stroke="currentColor"
        strokeWidth="2"
      />
      <circle cx="12.5" cy="9" r="1.9" fill="var(--accent)" />
    </>,
  );

// Pipelines — three linked nodes, the lead node live in ember (the chain in motion).
export const BrandPipelines = ({ size = 20, className }: IconProps) =>
  brand(
    size,
    className,
    <>
      <path d="M8.2 10.9 L15.8 7.1 M8.2 13.1 L15.8 16.9" stroke="currentColor" strokeWidth="2" />
      <circle cx="6" cy="12" r="2.4" stroke="currentColor" strokeWidth="2" />
      <circle cx="18" cy="6" r="2.4" stroke="currentColor" strokeWidth="2" />
      <circle cx="18" cy="18" r="2.4" fill="var(--accent)" />
    </>,
  );

/* ---------- run status badge ---------- */

export function StatusBadge({ run }: { run: Run }) {
  if (run.status !== "completed") {
    return (
      <span className="badge running">
        <IconDots className="spin" /> {run.status ?? "unknown"}
      </span>
    );
  }
  if (run.conclusion === "success") return <span className="badge ok"><IconCheck /> success</span>;
  if (run.conclusion === "failure") return <span className="badge fail"><IconX /> failure</span>;
  return <span className="badge neutral">{run.conclusion ?? "completed"}</span>;
}

/* ---------- severity + drift pills ---------- */

const SEV = new Set(["critical", "high", "medium", "low", "info"]);
export function SeverityPill({ severity }: { severity: string }) {
  const cls = SEV.has(severity) ? severity : "info";
  return <span className={`sev ${cls}`}>{severity}</span>;
}

export function DriftPill({ severity }: { severity: string | null }) {
  const map: Record<string, string> = {
    structural: "fail",
    content: "running",
    minor: "neutral",
    identical: "ok",
  };
  const label = severity ?? "unknown";
  return <span className={`badge ${map[label] ?? "neutral"}`}>{label}</span>;
}

/* ---------- score gauge ---------- */

export function ScoreRing({ score }: { score: number }) {
  const r = 56;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score)) / 100;
  const ring = score >= 80 ? "var(--ok-soft)" : score >= 50 ? "var(--warn-soft)" : "var(--fail-soft)";
  const color = score >= 80 ? "var(--ok)" : score >= 50 ? "var(--warn)" : "var(--fail)";
  return (
    <div className="gauge">
      <svg width="132" height="132" viewBox="0 0 132 132">
        <circle cx="66" cy="66" r={r} fill="none" stroke="var(--surface-3)" strokeWidth="12" />
        <circle
          cx="66"
          cy="66"
          r={r}
          fill="none"
          stroke={ring}
          strokeWidth="12"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - pct)}
          transform="rotate(-90 66 66)"
          style={{ transition: "stroke-dashoffset 0.6s ease" }}
        />
      </svg>
      <div className="score-num">
        <span className="score-val" style={{ color }}>{score}</span>
        <span className="score-cap">posture</span>
      </div>
    </div>
  );
}

/* ---------- states ---------- */

export function EmptyState({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}

export function ErrorBanner({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="error-banner">Couldn’t load data: {msg}</div>;
}

export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="table-wrap">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton skeleton-row" style={{ width: `${90 - (i % 3) * 12}%` }} />
      ))}
    </div>
  );
}

export function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}
