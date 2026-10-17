// Pieces shared by the surge landing page and the surge map.

// Mark: a 3x3 grid with water rising through it, after the fire mark.
export function SurgeLogo({ compact = false }) {
  return (
    <span className="logo">
      <svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true">
        {[0, 1, 2].flatMap((r) => [0, 1, 2].map((c) => {
          const wet = r === 2 || (r === 1 && c === 0);
          const crest = r === 1 && c === 1;
          return (
            <rect key={`${r}${c}`} x={c * 6} y={r * 6} width="5" height="5" rx="1"
                  fill={crest ? "var(--foam)" : wet ? "var(--tide)" : "var(--ridge-2)"} />
          );
        }))}
      </svg>
      {!compact && <span className="logo-word">SurgeTrace</span>}
    </span>
  );
}

export const SURGE_LEVEL_COLOR = {
  none: "var(--lichen)",
  watch: "var(--sky)",
  warn: "var(--flare)",
  evacuate: "var(--ember)",
};
export const SURGE_LEVEL_LABEL = { none: "No alert", watch: "Watch", warn: "Get ready", evacuate: "Leave now" };

export function catLabel(cat) {
  return cat >= 1 ? `Category ${cat} hurricane` : cat === 0 ? "Tropical storm" : "Tropical depression";
}
