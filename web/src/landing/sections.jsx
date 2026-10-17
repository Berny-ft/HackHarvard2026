import { Fragment, useMemo, useState } from "react";
import { EVALS, GATEWAY, SANDBOX } from "./snippets.js";
import { LEVEL_COLOR, LEVEL_LABEL, hrs, pct } from "../shared.jsx";

const SCOPES = [["all", "Both fires"], ["eaton_2025", "Eaton"], ["palisades_2025", "Palisades"]];
const POLICIES = [
  { key: "radius", name: "Radius", note: "Fixed 3, 8 and 15 km rings. Blind to wind. Baseline." },
  { key: "rules", name: "Wind rules", note: "Downwind arrival estimate plus rings. Unconfirmed reports capped at warn." },
  { key: "llm", name: "LLM", note: "Same signals, decided by a model through the Chalk Model Gateway." },
];
// Higher is better unless `low`.
const METRICS = [
  { key: "warned_in_time", label: "Warned in time", fmt: pct },
  { key: "evacuate_in_time", label: "Evacuated in time", fmt: pct },
  { key: "median_warn_lead_h", label: "Warning lead, median", fmt: hrs },
  { key: "median_evacuate_lead_h", label: "Evacuate lead, median", fmt: hrs },
  { key: "over_evacuation_rate", label: "Needless evacuations", fmt: pct, low: true },
  { key: "false_warning_rate", label: "False warnings", fmt: pct, low: true },
  { key: "alerts_per_subscriber", label: "Alerts each", fmt: (v) => (v == null ? "–" : v.toFixed(1)), low: true },
];

export function EvalConsole({ replay }) {
  const [scope, setScope] = useState("all");
  const summary = replay?.summary ?? {};
  const best = useMemo(() => {
    const out = {};
    for (const m of METRICS) {
      const vals = POLICIES.map((p) => summary[p.key]?.[scope]?.[m.key]).filter((v) => v != null);
      if (vals.length < 2) continue;
      const top = m.low ? Math.min(...vals) : Math.max(...vals);
      if (vals.filter((v) => v === top).length === 1) out[m.key] = top;
    }
    return out;
  }, [summary, scope]);
  const n = summary.rules?.[scope];

  return (
    <div className="console">
      <div className="console-bar">
        <div className="console-crumbs">
          <span>Evaluations</span><span className="sep">/</span><b>firetrace-sentinel</b>
        </div>
        <div className="tabs" role="tablist" aria-label="Fire">
          {SCOPES.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={scope === k} className={scope === k ? "on" : ""}
                    onClick={() => setScope(k)}>{label}</button>
          ))}
        </div>
      </div>
      <div className="console-meta">
        <span>Dataset <b className="num">firetrace-replay-n100</b></span>
        <span><b className="num">{n ? n.destroyed_n : "–"}</b> destroyed homes</span>
        <span><b className="num">{n ? n.outside_n : "–"}</b> homes outside the burn</span>
        <span>Ground truth <b>first satellite detection within 750 m</b></span>
      </div>

      <div className="table-scroll">
        <table className="eval-table">
          <thead>
            <tr>
              <th scope="col">Policy</th>
              {METRICS.map((m) => <th key={m.key} scope="col">{m.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {POLICIES.map((p) => {
              const row = summary[p.key]?.[scope];
              return (
                <tr key={p.key} className={row ? "" : "pending"}>
                  <th scope="row">
                    <span className="policy-name">{p.name}</span>
                    <span className="policy-note">{p.note}</span>
                  </th>
                  {row ? METRICS.map((m) => {
                    const v = row[m.key];
                    const top = best[m.key] != null && v === best[m.key];
                    return (
                      <td key={m.key} className={`num ${top ? "best" : ""}`}>
                        {m.fmt(v)}
                        {m.key.endsWith("in_time") && v != null && <Meter value={v} />}
                      </td>
                    );
                  }) : (
                    <td colSpan={METRICS.length} className="pending-cell">
                      Runs through the Chalk Model Gateway. Results land here after the next gateway run.
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {replay && <LeadTimes households={replay.households} scope={scope} />}
    </div>
  );
}

function Meter({ value }) {
  return (
    <span className="meter" aria-hidden="true">
      <span style={{ transform: `scaleX(${value})` }} />
    </span>
  );
}

// Histogram of head start per destroyed home, rules policy. Negative = told after the fire arrived.
function LeadTimes({ households, scope }) {
  const rows = households.filter((h) => h.category === "destroyed" && h.arrival_h != null &&
    (scope === "all" || h.incident === scope));
  const MIN = -4, MAX = 24;
  const bins = (level) => {
    const counts = Array(MAX - MIN + 1).fill(0);
    let never = 0;
    for (const h of rows) {
      const first = h.first[level];
      if (first == null) { never++; continue; }
      const lead = Math.max(MIN, Math.min(MAX, Math.floor(h.arrival_h - first)));
      counts[lead - MIN]++;
    }
    return { counts, never };
  };
  const series = [["warn", "Get ready"], ["evacuate", "Leave now"]].map(([k, label]) => ({ k, label, ...bins(k) }));
  const peak = Math.max(1, ...series.flatMap((s) => s.counts));
  const W = 720, H = 74, bw = W / (MAX - MIN + 1);
  const x0 = (0 - MIN) * bw;

  return (
    <div className="leads">
      <div className="leads-head">
        <h3>Head start per destroyed home</h3>
        <p>Wind-rules policy, {rows.length} homes. Hours between the alert and the fire's observed arrival.</p>
      </div>
      {series.map((s) => (
        <div key={s.k} className="leads-row">
          <div className="leads-label">
            <span className="dot" style={{ background: LEVEL_COLOR[s.k] }} />{s.label}
          </div>
          <svg viewBox={`0 0 ${W} ${H + 18}`} preserveAspectRatio="none" role="img"
               aria-label={`${s.label}: ${s.counts.slice(-MIN).reduce((a, b) => a + b, 0)} of ${rows.length} in time`}>
            <defs>
              <pattern id={`late-${s.k}`} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width="5" height="5" fill="rgba(255,106,43,0.18)" />
                <line x1="0" y1="0" x2="0" y2="5" stroke="var(--ember)" strokeWidth="2.2" />
              </pattern>
            </defs>
            <line x1={0} x2={W} y1={H} y2={H} className="leads-axis" />
            {s.counts.map((c, i) => {
              if (!c) return null;
              const h = Math.max(2, (c / peak) * (H - 6));
              const late = i + MIN < 0;
              return (
                <rect key={i} x={i * bw + 1.5} y={H - h} width={bw - 3} height={h} rx="1.5"
                      fill={late ? `url(#late-${s.k})` : LEVEL_COLOR[s.k]} opacity={late ? 1 : 0.85}
                      stroke={late ? "var(--ember)" : "none"} strokeWidth="1">
                  <title>{`${c} home${c > 1 ? "s" : ""}: ${i + MIN >= MAX ? `${MAX}+` : i + MIN} h ahead`}</title>
                </rect>
              );
            })}
            <line x1={x0} x2={x0} y1={0} y2={H + 4} className="leads-zero" />
            {[0, 6, 12, 18, 24].map((t) => (
              <text key={t} x={(t - MIN) * bw + bw / 2} y={H + 15} textAnchor="middle" className="leads-tick">
                {t === 24 ? "24h+" : `${t}h`}
              </text>
            ))}
          </svg>
          <div className="leads-note num">{s.never ? `${s.never} never` : ""}</div>
        </div>
      ))}
      <p className="leads-legend">
        <span><i className="hatch" />Left of the dashed line: the alert came after the fire arrived.</span>
      </p>
    </div>
  );
}

export function Household({ replay }) {
  const home = useMemo(() => replay?.households.find((h) =>
    h.incident === "eaton_2025" && h.category === "destroyed" && h.alerts.length >= 3 &&
    h.first.evacuate != null && h.first.evacuate >= 6 && h.first.evacuate < h.arrival_h), [replay]);
  if (!home) return <div className="household skeleton" />;

  const span = 48;
  const at = (h) => `${(Math.min(h, span) / span) * 100}%`;
  const start = new Date("2025-01-07T18:00:00");
  const clock = (h) => new Date(start.getTime() + h * 3600e3)
    .toLocaleString("en-US", { weekday: "short", hour: "numeric", minute: "2-digit" });

  return (
    <div className="household">
      <div className="track" aria-hidden="true">
        <div className="track-line" />
        {home.alerts.filter((a) => a.hour <= span).map((a) => (
          <span key={a.hour} className="track-alert" style={{ left: at(a.hour), "--c": LEVEL_COLOR[a.level] }}>
            <i />{a.hour > home.arrival_h ? "Stands down" : LEVEL_LABEL[a.level]}
          </span>
        ))}
        <span className="track-fire" style={{ left: at(home.arrival_h) }}>
          <i />Fire arrives
        </span>
        {[0, 12, 24, 36, 48].map((t) => (
          <span key={t} className="track-tick num" style={{ left: at(t) }}>{t === 0 ? "Ignition" : `${t}h`}</span>
        ))}
      </div>

      <ol className="messages">
        {home.alerts.map((a, i) => {
          const lead = home.arrival_h - a.hour;
          return (
            <Fragment key={a.hour}>
              {i > 0 && home.alerts[i - 1].hour < home.arrival_h && a.hour >= home.arrival_h && (
                <li className="arrival-row">
                  <span className="num">T+{home.arrival_h.toFixed(1)}h</span>
                  Satellites detect fire within 750 m of the home. CAL FIRE later records it destroyed.
                </li>
              )}
              <li className="message" style={{ "--c": LEVEL_COLOR[a.level] }}>
                <div className="message-meta">
                  <span className="level">{a.hour > home.arrival_h ? `Stands down to ${LEVEL_LABEL[a.level].toLowerCase()}` : LEVEL_LABEL[a.level]}</span>
                  <span className="num">{clock(a.hour)}</span>
                  <span className="num muted">
                    {lead > 0 ? `${lead.toFixed(1)} h before the fire` : a.hour > home.arrival_h ? "After the front passed" : ""}
                  </span>
                </div>
                <p>{a.message}</p>
              </li>
            </Fragment>
          );
        })}
      </ol>
    </div>
  );
}

const STEPS = [
  {
    title: "Watch the feeds",
    body: "Satellite hot spots from NASA FIRMS, NIFC incidents and perimeters, NWS Red Flag Warnings and an hourly fire-weather forecast for 40 points across both counties.",
  },
  {
    title: "Score each home",
    body: "Distance and bearing to the nearest fire, whether the home is downwind, a Rothermel arrival estimate, gusts, 1-hour fuel moisture and a 10-year burn likelihood.",
  },
  {
    title: "Choose a level",
    body: "None, watch, get ready or leave now. A policy decides, and only a change of level sends anything, so nobody gets the same alert twice.",
  },
  {
    title: "Tell people and their agents",
    body: "A text message for the person. An HMAC-signed webhook for their personal agent, which can call back over MCP for detail and tailor the advice.",
  },
];

export function Pipeline() {
  return (
    <ol className="pipeline">
      {STEPS.map((s, i) => (
        <li key={s.title}>
          <span className="step num">{i + 1}</span>
          <h3>{s.title}</h3>
          <p>{s.body}</p>
        </li>
      ))}
      <li className="pipeline-mcp">
        <h3>MCP tools a personal agent can call</h3>
        <ul className="tools num">
          {["get_threat", "regional_overview", "subscribe", "recent_alerts", "replay_threat"].map((t) => <li key={t}>{t}</li>)}
        </ul>
      </li>
    </ol>
  );
}

export function Findings({ replay }) {
  const r = replay?.summary;
  const radius = r?.radius?.all?.evacuate_in_time;
  const rules = r?.rules?.all?.evacuate_in_time;
  const items = [
    {
      figure: radius != null && rules != null ? `${pct(radius)} / ${pct(rules)}` : "92.5% / 82.5%",
      caption: "evacuated in time: radius, then wind rules",
      title: "A plain distance ring is hard to beat",
      body: "On these two fires the wind-aware arrival estimate adds little over distance. Satellite passes come hours apart, which limits how well any model can track the front between them.",
    },
    {
      figure: "1.8–2.7 h",
      caption: "from ignition to the first satellite confirmation",
      title: "Trusting every report wins replays and fails live",
      body: "NIFC lists about 48 active incidents in the region on a normal day, most of them tiny. Treating each as a fire that needs evacuating would wear out everyone's trust, so unconfirmed reports top out at get ready. That choice is where the LLM policy should do better, for example under a PDS Red Flag with 60 mph gusts.",
    },
    {
      figure: "20%+",
      caption: "needless evacuations when old detections count as a live front",
      title: "Old detections aren't the fire front",
      body: "Giving full credit for the distance a fire might have travelled since each detection over-warned badly on Eaton. Fitted on Eaton and tested on Palisades, that credit lands at zero.",
    },
  ];
  return (
    <div className="findings">
      {items.map((f) => (
        <article key={f.title} className="finding">
          <div className="finding-figure">
            <span className="num">{f.figure}</span>
            <small>{f.caption}</small>
          </div>
          <div>
            <h3>{f.title}</h3>
            <p>{f.body}</p>
          </div>
        </article>
      ))}
      <p className="caveat">
        Caveats: arrival times are limited to satellite overpasses, so lead times are approximate. Hourly reanalysis
        weather stands in for a forecast, which flatters every policy. Two fires is a small sample.
      </p>
    </div>
  );
}

const STACK = [
  {
    key: "gateway", name: "Model Gateway", file: "agent/sentinel/policy.py", code: GATEWAY,
    body: "The LLM policy and the alert-quality judge call models through the gateway's OpenAI-compatible router. The model is only asked when the rules see something, so cost stays bounded.",
  },
  {
    key: "evals", name: "Evaluations", file: "agent/evals/chalk_eval.py", code: EVALS,
    body: "One dataset, one task per policy, five deterministic scorers and an LLM judge for message quality, grouped in the firetrace-sentinel suite. Every run is pinned to a git SHA.",
  },
  {
    key: "sandbox", name: "Sandboxes", file: "agent/deploy/sandbox.py", code: SANDBOX,
    body: "The alert loop and the MCP endpoint run around the clock in a Chalk sandbox, with subscribers and the alert log on a Chalk Volume so a restart loses nothing.",
  },
];

export function ChalkStack() {
  const [k, setK] = useState("evals");
  const cur = STACK.find((s) => s.key === k);
  return (
    <div className="stack">
      <div className="stack-list" role="tablist" aria-label="Chalk components">
        {STACK.map((s) => (
          <button key={s.key} role="tab" aria-selected={k === s.key} className={k === s.key ? "on" : ""}
                  onClick={() => setK(s.key)}>
            <span className="stack-name">{s.name}</span>
            <span className="stack-body">{s.body}</span>
          </button>
        ))}
      </div>
      <div className="code" key={cur.key}>
        <div className="code-bar"><span className="num">{cur.file}</span></div>
        <pre><code>{cur.code.split("\n").map((line, i) => (
          <span key={i} className="code-line"><span className="ln num">{i + 1}</span>{highlight(line)}{"\n"}</span>
        ))}</code></pre>
      </div>
    </div>
  );
}

// A small Python highlighter: enough for the excerpts above.
const PY = /(#.*$)|("""[^]*?"""|"(?:[^"\\]|\\.)*"|f"(?:[^"\\]|\\.)*")|(@[\w.]+)|\b(def|class|return|from|import|if|and|not|for|in|or|None|True|False)\b|\b(\d+(?:\.\d+)?)\b|\b([A-Za-z_]\w*)(?=\()/g;

function highlight(line) {
  const out = [];
  let last = 0, m;
  PY.lastIndex = 0;
  while ((m = PY.exec(line))) {
    if (m.index > last) out.push(line.slice(last, m.index));
    const cls = m[1] ? "c-com" : m[2] ? "c-str" : m[3] ? "c-dec" : m[4] ? "c-kw" : m[5] ? "c-num" : "c-fn";
    out.push(<span key={m.index} className={cls}>{m[0]}</span>);
    last = m.index + m[0].length;
  }
  if (last < line.length) out.push(line.slice(last));
  return out;
}
