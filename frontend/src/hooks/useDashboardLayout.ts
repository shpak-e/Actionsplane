import { useCallback, useSyncExternalStore } from "react";

/**
 * Customizable Home layout, persisted in this browser (localStorage). We store a single ordered
 * list of widget ids plus which are hidden; the DashboardTab owns the widget catalog and renders in
 * this order. Unknown/new widget ids (added in a later release) are appended so the layout upgrades
 * gracefully instead of hiding new widgets.
 */

export const ALL_WIDGETS = [
  "posture",
  "severity",
  "drift",
  "radar",
  "runs",
  "campaigns",
  "pipelines",
  "fleet",
] as const;

export type WidgetId = (typeof ALL_WIDGETS)[number];

export type DashboardLayout = { order: WidgetId[]; hidden: WidgetId[] };

const KEY = "actionsplane.dashboard_layout";
const listeners = new Set<() => void>();
const DEFAULT: DashboardLayout = { order: [...ALL_WIDGETS], hidden: [] };

function read(): DashboardLayout {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return DEFAULT;
    const parsed = JSON.parse(raw) as Partial<DashboardLayout>;
    const known = new Set<string>(ALL_WIDGETS);
    const order = (parsed.order ?? []).filter((w): w is WidgetId => known.has(w));
    // Append any widget ids not present yet (forward-compat when we add widgets).
    for (const w of ALL_WIDGETS) if (!order.includes(w)) order.push(w);
    const hidden = (parsed.hidden ?? []).filter((w): w is WidgetId => known.has(w));
    return { order, hidden };
  } catch {
    return DEFAULT;
  }
}

function write(layout: DashboardLayout): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(layout));
  } catch {
    /* best-effort */
  }
  for (const n of listeners) n();
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY || e.key === null) cb();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", onStorage);
  };
}

// Cache the parsed snapshot so useSyncExternalStore gets a stable reference between writes
// (returning a fresh object every call would loop forever).
let snapshot = read();
let snapshotRaw = safeRaw();
function safeRaw(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}
function getSnapshot(): DashboardLayout {
  const raw = safeRaw();
  if (raw !== snapshotRaw) {
    snapshotRaw = raw;
    snapshot = read();
  }
  return snapshot;
}

export function useDashboardLayout() {
  const layout = useSyncExternalStore(subscribe, getSnapshot, () => DEFAULT);

  const isVisible = useCallback((id: WidgetId) => !layout.hidden.includes(id), [layout.hidden]);

  const toggle = useCallback(
    (id: WidgetId) => {
      const hidden = layout.hidden.includes(id)
        ? layout.hidden.filter((w) => w !== id)
        : [...layout.hidden, id];
      write({ order: layout.order, hidden });
    },
    [layout],
  );

  const move = useCallback(
    (id: WidgetId, dir: -1 | 1) => {
      const order = [...layout.order];
      const i = order.indexOf(id);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= order.length) return;
      [order[i], order[j]] = [order[j], order[i]];
      write({ order, hidden: layout.hidden });
    },
    [layout],
  );

  const reset = useCallback(() => write(DEFAULT), []);

  return { layout, isVisible, toggle, move, reset };
}
