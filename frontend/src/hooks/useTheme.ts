import { useCallback, useState } from "react";

export type Theme = "dark" | "light";

/**
 * Two saved themes — "dark" (Console/ember) and "light" (Blueprint Cobalt). The initial value is
 * applied to <html data-theme> before first paint by an inline script in index.html (no flash);
 * this hook keeps React in sync and persists changes to localStorage.
 */
function current(): Theme {
  const attr = document.documentElement.getAttribute("data-theme");
  return attr === "light" ? "light" : "dark";
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(current);

  const toggle = useCallback(() => {
    setTheme((prev) => {
      const next: Theme = prev === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      document
        .querySelector('meta[name="theme-color"]')
        ?.setAttribute("content", next === "dark" ? "#0B0C0E" : "#C2CDDE");
      try {
        localStorage.setItem("ap-theme", next);
      } catch {
        /* storage disabled — the in-memory + attribute state still applies for this session */
      }
      return next;
    });
  }, []);

  return { theme, toggle };
}
