const W = 300, H = 84, PAD = { l: 30, r: 6, t: 6, b: 16 };

function fmt(v, digits = 0) {
  if (v == null) return "–";
  if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k`;
  return digits ? v.toFixed(digits) : Math.round(v).toString();
}

// Small SVG area/line chart: the past is solid, the rest of the fire is a faint preview.
// `digits` shows decimals (water levels in feet); values below zero extend the axis down.
export function Chart({ title, unit, rows, series, hour, digits = 0 }) {
  const n = rows.length - 1;
  const vals = series.flatMap((s) => rows.map((r) => r[s.key]).filter((v) => v != null));
  const max = Math.max(1, ...vals);
  const lo = Math.min(0, ...vals);
  const x = (i) => PAD.l + (i / n) * (W - PAD.l - PAD.r);
  const y = (v) => H - PAD.b - (((v ?? lo) - lo) / (max - lo)) * (H - PAD.t - PAD.b);
  const h = Math.min(hour, n);
  const cur = rows[h];

  return (
    <div className="chart">
      <div className="chart-head">
        <span className="chart-title">{title}</span>
        <span className="chart-val">
          {series.map((s, i) => (
            <span key={s.key} title={s.label}>
              {i > 0 && <span className="muted"> / </span>}
              <b className="num" style={{ color: s.color }}>{fmt(cur[s.key], digits)}</b>
            </span>
          ))}
          <span className="muted"> {unit}</span>
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
        <defs>
          {series.filter((s) => s.fill).map((s) => (
            <linearGradient key={s.key} id={`g-${s.key}`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" stopColor={s.color} stopOpacity="0.35" />
              <stop offset="1" stopColor={s.color} stopOpacity="0.02" />
            </linearGradient>
          ))}
        </defs>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(lo + (max - lo) * f)} y2={y(lo + (max - lo) * f)} className="grid" />
            <text x={PAD.l - 5} y={y(lo + (max - lo) * f) + 3} textAnchor="end" className="axis">{fmt(lo + (max - lo) * f)}</text>
          </g>
        ))}
        {Array.from({ length: Math.floor(n / 24) + 1 }, (_, i) => i * 24).map((t) => (
          <text key={t} x={x(t)} y={H - 3} textAnchor={t === 0 ? "start" : "middle"} className="axis">{t}h</text>
        ))}
        {series.map((s) => {
          const pt = (r, i) => (r[s.key] == null ? null : `${x(i)},${y(r[s.key])}`);
          const pts = rows.map(pt).filter(Boolean).join(" ");
          const past = rows.slice(0, h + 1).map(pt).filter(Boolean).join(" ");
          return (
            <g key={s.key}>
              {s.fill && <polygon points={`${x(0)},${y(lo)} ${past} ${x(h)},${y(lo)}`} fill={`url(#g-${s.key})`} />}
              <polyline points={pts} fill="none" stroke={s.color} strokeWidth="1" opacity="0.22" vectorEffect="non-scaling-stroke" />
              <polyline points={past} fill="none" stroke={s.color} strokeWidth="1.6" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
            </g>
          );
        })}
        <line x1={x(h)} x2={x(h)} y1={PAD.t} y2={H - PAD.b} className="cursor" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}
