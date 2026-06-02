import type { ReactNode } from "react";
import type { Run } from "../types";

/* ---------- inline icons (stroke = currentColor) ---------- */

type IconProps = { size?: number; className?: string };
const svg = (size: number, className: string | undefined, children: ReactNode) => (
  <svg
    className={`gi ${className ?? ""}`}
    width={size}
    height={size}
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.6"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    {children}
  </svg>
);

export const IconCheck = ({ size = 14, className }: IconProps) =>
  svg(size, className, <path d="M13.5 4.5 6 12 2.5 8.5" />);
export const IconX = ({ size = 14, className }: IconProps) =>
  svg(size, className, <path d="M12 4 4 12M4 4l8 8" />);
export const IconDots = ({ size = 14, className }: IconProps) =>
  svg(size, className, <circle cx="8" cy="8" r="5.5" strokeDasharray="2.4 2.4" />);
export const IconClock = ({ size = 14, className }: IconProps) =>
  svg(size, className, <><circle cx="8" cy="8" r="6" /><path d="M8 5v3.2l2 1.3" /></>);
export const IconRepo = ({ size = 14, className }: IconProps) =>
  svg(size, className, <><path d="M3 2.5h8.5A1.5 1.5 0 0 1 13 4v9.5H4.5A1.5 1.5 0 0 1 3 12V2.5Z" /><path d="M3 11.5h10" /></>);
export const IconExternal = ({ size = 13, className }: IconProps) =>
  svg(size, className, <><path d="M6.5 3.5H3.5V12.5H12.5V9.5" /><path d="M9 3.5h3.5V7M12.5 3.5 7 9" /></>);
export const IconRefresh = ({ size = 13, className }: IconProps) =>
  svg(size, className, <><path d="M13 3.5v3h-3" /><path d="M12.6 6.5A5 5 0 1 0 13 9" /></>);
export const IconLogs = ({ size = 13, className }: IconProps) =>
  svg(size, className, <><path d="M3 3.5h10M3 8h10M3 12.5h6" /></>);
export const IconShield = ({ size = 14, className }: IconProps) =>
  svg(size, className, <path d="M8 2 13 4v4c0 3-2.2 5-5 6-2.8-1-5-3-5-6V4l5-2Z" />);
export const IconActivity = ({ size = 14, className }: IconProps) =>
  svg(size, className, <path d="M1.5 8h3l2-4.5 3 9 2-4.5h3" />);
export const IconLayers = ({ size = 14, className }: IconProps) =>
  svg(size, className, <><path d="M8 2 14 5 8 8 2 5l6-3Z" /><path d="M2 8.5 8 11.5 14 8.5M2 11 8 14 14 11" /></>);
export const IconBranch = ({ size = 13, className }: IconProps) =>
  svg(size, className, <><circle cx="4.5" cy="4" r="1.8" /><circle cx="4.5" cy="12" r="1.8" /><circle cx="11.5" cy="4" r="1.8" /><path d="M4.5 5.8v4.4M11.5 5.8c0 3-3.5 2.2-3.5 4.4" /></>);
export const IconGraph = ({ size = 14, className }: IconProps) =>
  svg(size, className, <><circle cx="3.5" cy="8" r="2" /><circle cx="12.5" cy="3.5" r="2" /><circle cx="12.5" cy="12.5" r="2" /><path d="M5.4 7l5.3-2.6M5.4 9l5.3 2.6" /></>);
export const IconArrowRight = ({ size = 14, className }: IconProps) =>
  svg(size, className, <><path d="M2.5 8h10M9 4.5 12.5 8 9 11.5" /></>);

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
          stroke={color}
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
