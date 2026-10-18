// Small pieces shared by the landing page and the map app.
import { SurgeLogo } from "./surge/common.jsx";

export const pct = (v, digits = 1) => (v == null ? "–" : `${+(v * 100).toFixed(digits)}%`);
export const hrs = (v) => (v == null ? "–" : `${v.toFixed(1)} h`);

// Client-side navigation between the two pages without a router.
export function navigate(e) {
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
  e.preventDefault();
  const href = e.currentTarget.getAttribute("href");
  window.history.pushState({}, "", href);
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo(0, 0);
}

// Mark: a 3x3 grid of cells with fire moving through it, after the hero field.
export function Logo({ compact = false }) {
  return (
    <span className="logo">
      <svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true">
        {[0, 1, 2].flatMap((r) => [0, 1, 2].map((c) => {
          const lit = (r === 0 && c === 2) || (r === 1 && c >= 1) || (r === 2 && c === 2);
          const hot = r === 1 && c === 2;
          return (
            <rect key={`${r}${c}`} x={c * 6} y={r * 6} width="5" height="5" rx="1"
                  fill={hot ? "var(--flare)" : lit ? "var(--ember)" : "var(--ridge-2)"} />
          );
        }))}
      </svg>
      {!compact && <span className="logo-word">Trace</span>}
    </span>
  );
}

// Nav brand for the landing pages: the fire and water marks switch between the two modes.
export function Brand({ mode }) {
  return (
    <span className="brand">
      <span className="mode-switch" role="group" aria-label="Hazard">
        <a href="/" onClick={navigate} className={mode === "fire" ? "on" : ""} aria-label="Wildfire"
           aria-current={mode === "fire" ? "page" : undefined}><Logo compact /></a>
        <a href="/surge" onClick={navigate} className={mode === "water" ? "on" : ""} aria-label="Storm surge"
           aria-current={mode === "water" ? "page" : undefined}><SurgeLogo compact /></a>
      </span>
      <span className="logo-word">Trace</span>
    </span>
  );
}

export const LEVEL_COLOR = {
  none: "var(--lichen)",
  watch: "var(--sky)",
  warn: "var(--flare)",
  evacuate: "var(--ember)",
};
export const LEVEL_LABEL = { none: "No alert", watch: "Watch", warn: "Get ready", evacuate: "Leave now" };
