const W = 300, H = 92, PAD = { l: 34, r: 8, t: 8, b: 18 };

function fmt(v) {
  if (v >= 1000) return `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k`;
  return Math.round(v).toString();
}

// Tiny SVG area/line chart with an hour cursor.
export function Chart({ title, unit, rows, series, hour }) {
  const n = rows.length - 1;
  const max = Math.max(1, ...series.flatMap((s) => rows.map((r) => r[s.key])));
  const x = (i) => PAD.l + (i / n) * (W - PAD.l - PAD.r);
  const y = (v) => H - PAD.b - (v / max) * (H - PAD.t - PAD.b);
  const cur = rows[Math.min(hour, n)];

  return (
    <div className="chart">
      <div className="chart-head">
        <span>{title}</span>
        <span className="chart-val">
          {series.map((s) => (
            <span key={s.key} style={{ color: s.color }}>{fmt(cur[s.key])}{s.suffix ?? ""} </span>
          ))}
          <span className="muted">{unit}</span>
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(max * f)} y2={y(max * f)} className="grid" />
            <text x={PAD.l - 4} y={y(max * f) + 3} textAnchor="end" className="axis">{fmt(max * f)}</text>
          </g>
        ))}
        {[0, 24, 48, 72].filter((h) => h <= n).map((h) => (
          <text key={h} x={x(h)} y={H - 4} textAnchor="middle" className="axis">{h}h</text>
        ))}
        {series.map((s) => {
          const pts = rows.map((r, i) => `${x(i)},${y(r[s.key])}`).join(" ");
          const past = rows.slice(0, hour + 1).map((r, i) => `${x(i)},${y(r[s.key])}`).join(" ");
          return (
            <g key={s.key}>
              {s.fill && <polygon points={`${x(0)},${y(0)} ${past} ${x(Math.min(hour, n))},${y(0)}`} fill={s.color} opacity="0.18" />}
              <polyline points={pts} fill="none" stroke={s.color} strokeWidth="1.2" strokeDasharray="3 2" opacity="0.45" />
              <polyline points={past} fill="none" stroke={s.color} strokeWidth="1.8" />
            </g>
          );
        })}
        <line x1={x(Math.min(hour, n))} x2={x(Math.min(hour, n))} y1={PAD.t} y2={H - PAD.b} className="cursor" />
      </svg>
    </div>
  );
}
