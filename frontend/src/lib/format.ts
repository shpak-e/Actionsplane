// Small, dependency-free formatting helpers shared across the dashboard.

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : "—";
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const sec = Math.round((Date.now() - then) / 1000);
  if (sec < 0) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function duration(start: string | null, end: string | null): string {
  if (!start || !end) return "—";
  const s = (new Date(end).getTime() - new Date(start).getTime()) / 1000;
  if (Number.isNaN(s) || s < 0) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${Math.round(s % 60)}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function fmtPct(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

export function fmtSecs(v: number | null | undefined): string {
  if (v == null) return "—";
  return v < 60 ? `${v.toFixed(0)}s` : `${(v / 60).toFixed(1)}m`;
}
