import { Fragment, useMemo, useState } from "react";
import { hrs, pct } from "../shared.jsx";
import { SURGE_LEVEL_COLOR as LEVEL_COLOR, SURGE_LEVEL_LABEL as LEVEL_LABEL } from "./common.jsx";
import { CHALK_EVAL, GATEWAY, LEARN } from "./snippets.js";
import { highlight } from "../landing/sections.jsx";

const SCOPES = [["all", "All four"], ["held_out", "Held out"], ["sample", "LLM sample"], ["sandy_2012", "Sandy"],
                ["matthew_2016", "Matthew"], ["irma_2017", "Irma"], ["isabel_2003", "Isabel"]];
const POLICIES = [
  { key: "zones", name: "Official zones", note: "Evacuation zones by ground height, ordered by storm category. What most counties do." },
  { key: "rules", name: "Address rules", note: "This address's forecast flood depth and timing. Thresholds fitted on Sandy and Matthew." },
  { key: "llm", name: "LLM", note: "Same signals, decided by a model through the Chalk Model Gateway." },
  { key: "llm_learned", name: "LLM + lessons", note: "Plus lessons it wrote itself from its mistakes on the train storms." },
];
const METRICS = [
  { key: "warned_in_time", label: "Warned in time", fmt: pct },
  { key: "evacuate_in_time", label: "Evacuated in time", fmt: pct },
  { key: "evacuate_12h_ahead", label: "Evacuated 12 h+ ahead", fmt: pct },
  { key: "median_evacuate_lead_h", label: "Evacuate lead, median", fmt: hrs },
  { key: "needless_evacuation_rate", label: "Needless evacuations", fmt: pct, low: true },
  { key: "false_warning_rate", label: "False warnings", fmt: pct, low: true },
  { key: "alerts_per_household", label: "Alerts each", fmt: (v) => (v == null ? "–" : v.toFixed(1)), low: true },
];

export function EvalConsole({ replay }) {
  const [scope, setScope] = useState("all");
  const summary = useMemo(() => {
    const s = { ...(replay?.summary ?? {}) };
    // The LLM runs cover a sample of the held-out storms; every policy is scored on that same sample.
    const l = replay?.learn;
    if (l) {
      s.zones = { ...s.zones, sample: l.held_out_zones };
      s.rules = { ...s.rules, sample: l.held_out_rules };
      s.llm = { sample: l.held_out_before };
      s.llm_learned = { sample: l.held_out_after };
    }
    return s;
  }, [replay]);
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
        <div className="console-crumbs"><span>Evaluations</span><span className="sep">/</span><b>surgetrace-sentinel</b></div>
        <div className="tabs" role="tablist" aria-label="Storm">
          {SCOPES.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={scope === k} className={scope === k ? "on" : ""} onClick={() => setScope(k)}>{label}</button>
          ))}
        </div>
      </div>
      <div className="console-meta">
        <span>Dataset <b className="num">surgetrace-replay-n60</b></span>
        <span><b className="num">{n ? n.flooded_n : "–"}</b> homes that flooded</span>
        <span><b className="num">{n ? n.spared_n + n.high_n : "–"}</b> that stayed dry</span>
        <span>Ground truth <b>water 6 inches deep, from NOAA tide gauges</b></span>
      </div>
      <div className="table-scroll">
        <table className="eval-table">
          <thead>
            <tr><th scope="col">Policy</th>{METRICS.map((m) => <th key={m.key} scope="col">{m.label}</th>)}</tr>
          </thead>
          <tbody>
            {POLICIES.map((p) => {
              const row = summary[p.key]?.[scope];
              return (
                <tr key={p.key} className={row ? "" : "pending"}>
                  <th scope="row"><span className="policy-name">{p.name}</span><span className="policy-note">{p.note}</span></th>
                  {row ? METRICS.map((m) => {
                    const v = row[m.key];
                    const top = best[m.key] != null && v === best[m.key];
                    return (
                      <td key={m.key} className={`num ${top ? "best" : ""}`}>
                        {m.fmt(v)}
                        {(m.key.endsWith("in_time") || m.key.endsWith("ahead")) && v != null && <Meter value={v} />}
                      </td>
                    );
                  }) : (
                    <td colSpan={METRICS.length} className="pending-cell">
                      {p.key.startsWith("llm") ? "Scored on a sample of the held-out storms through the Chalk gateway. Pick “LLM sample”." : "Not run yet."}
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
  return <span className="meter" aria-hidden="true"><span style={{ transform: `scaleX(${value})` }} /></span>;
}

const HELD = ["irma_2017", "isabel_2003"];

function LeadTimes({ households, scope }) {
  const rows = households.filter((h) => h.category === "flooded" && h.arrival_h != null &&
    (scope === "all" || h.storm === scope || ((scope === "held_out" || scope === "sample") && HELD.includes(h.storm))));
  const MIN = -6, MAX = 48, STEP = 2;
  const nb = (MAX - MIN) / STEP + 1;
  const bins = (level) => {
    const counts = Array(nb).fill(0);
    let never = 0;
    for (const h of rows) {
      const first = h.first[level];
      if (first == null) { never++; continue; }
      const lead = Math.max(MIN, Math.min(MAX, h.arrival_h - first));
      counts[Math.floor((lead - MIN) / STEP)]++;
    }
    return { counts, never };
  };
  const series = [["warn", "Get ready"], ["evacuate", "Leave now"]].map(([k, label]) => ({ k, label, ...bins(k) }));
  const peak = Math.max(1, ...series.flatMap((s) => s.counts));
  const W = 720, H = 74, bw = W / nb;
  const x0 = ((0 - MIN) / STEP) * bw;
  return (
    <div className="leads">
      <div className="leads-head">
        <h3>Head start per flooded home</h3>
        <p>Address-rules policy, {rows.length} homes. Hours between the alert and the water reaching the home.</p>
      </div>
      {series.map((s) => (
        <div key={s.k} className="leads-row">
          <div className="leads-label"><span className="dot" style={{ background: LEVEL_COLOR[s.k] }} />{s.label}</div>
          <svg viewBox={`0 0 ${W} ${H + 18}`} preserveAspectRatio="none" role="img" aria-label={`${s.label} lead times`}>
            <defs>
              <pattern id={`slate-${s.k}`} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width="5" height="5" fill="rgba(255,95,109,0.18)" />
                <line x1="0" y1="0" x2="0" y2="5" stroke="var(--ember)" strokeWidth="2.2" />
              </pattern>
            </defs>
            <line x1={0} x2={W} y1={H} y2={H} className="leads-axis" />
            {s.counts.map((c, i) => {
              if (!c) return null;
              const h = Math.max(2, (c / peak) * (H - 6));
              const late = MIN + i * STEP < 0;
              return (
                <rect key={i} x={i * bw + 1.5} y={H - h} width={bw - 3} height={h} rx="1.5"
                      fill={late ? `url(#slate-${s.k})` : LEVEL_COLOR[s.k]} opacity={late ? 1 : 0.85}
                      stroke={late ? "var(--ember)" : "none"} strokeWidth="1">
                  <title>{`${c} home${c > 1 ? "s" : ""}: ${MIN + i * STEP}–${MIN + (i + 1) * STEP} h ahead`}</title>
                </rect>
              );
            })}
            <line x1={x0} x2={x0} y1={0} y2={H + 4} className="leads-zero" />
            {[0, 12, 24, 36, 48].map((t) => (
              <text key={t} x={((t - MIN) / STEP) * bw + bw / 2} y={H + 15} textAnchor="middle" className="leads-tick">
                {t === 48 ? "48h+" : `${t}h`}
              </text>
            ))}
          </svg>
          <div className="leads-note num">{s.never ? `${s.never} never` : ""}</div>
        </div>
      ))}
      <p className="leads-legend"><span><i className="hatch" />Left of the dashed line: the alert came after the water arrived.</span></p>
    </div>
  );
}

export function Household({ replay }) {
  // A Sandy home that flooded at least 2 ft deep and got the full sequence, ahead of the water.
  const home = useMemo(() => replay?.households
    .filter((h) => h.storm === "sandy_2012" && h.category === "flooded" && h.peak_depth_ft >= 2 && h.alerts.length >= 3 &&
                   h.first.evacuate != null && h.first.evacuate < h.arrival_h)
    .sort((a, b) => b.peak_depth_ft - a.peak_depth_ft)[0], [replay]);
  if (!home) return <div className="household skeleton" />;
  const from = 0, span = 96;
  const at = (h) => `${((Math.min(h, span) - from) / (span - from)) * 100}%`;
  const start = new Date("2012-10-27T00:00:00Z");
  const clock = (h) => new Date(start.getTime() + h * 3600e3)
    .toLocaleString("en-US", { timeZone: "America/New_York", weekday: "short", hour: "numeric", minute: "2-digit" });
  return (
    <div className="household">
      <div className="track" aria-hidden="true">
        <div className="track-line" />
        {home.alerts.filter((a) => a.hour <= span).map((a, i, all) => {
          // Only the first stand-down after the water gets a label; later steps down would overlap it.
          const after = a.hour > home.arrival_h;
          const label = !after ? LEVEL_LABEL[a.level] : i > 0 && all[i - 1].hour > home.arrival_h ? "" : "Stands down";
          return (
            <span key={a.hour} className="track-alert" style={{ left: at(a.hour), "--c": LEVEL_COLOR[a.level] }}>
              <i />{label}
            </span>
          );
        })}
        <span className="track-fire track-water" style={{ left: at(home.arrival_h) }}><i />Water arrives</span>
        {[0, 24, 48, 72, 96].map((t) => (
          <span key={t} className="track-tick num" style={{ left: at(t) }}>{clock(t).split(" ")[0]}</span>
        ))}
      </div>
      <ol className="messages">
        {home.alerts.map((a, i) => {
          const lead = home.arrival_h - a.hour;
          return (
            <Fragment key={a.hour}>
              {i > 0 && home.alerts[i - 1].hour < home.arrival_h && a.hour >= home.arrival_h && (
                <li className="arrival-row">
                  <span className="num">{clock(home.arrival_h)}</span>
                  Seawater stands 6 inches deep at the home, rising to {home.peak_depth_ft.toFixed(1)} ft (NOAA gauges, The Battery and Kings Point).
                </li>
              )}
              <li className="message" style={{ "--c": LEVEL_COLOR[a.level] }}>
                <div className="message-meta">
                  <span className="level">{a.hour > home.arrival_h ? `Stands down to ${LEVEL_LABEL[a.level].toLowerCase()}` : LEVEL_LABEL[a.level]}</span>
                  <span className="num">{clock(a.hour)}</span>
                  <span className="num muted">{lead > 0 ? `${lead.toFixed(1)} h before the water` : a.hour > home.arrival_h ? "After the peak" : ""}</span>
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
    title: "Read the official forecast",
    body: "Every NHC advisory: track, intensity and wind radii out to five days. The replay only ever sees advisories already issued at that hour, never the storm's true path.",
  },
  {
    title: "Forecast the water",
    body: "A parametric surge model (Holland wind field, pressure, onshore wind over the shelf) runs forward from what each tide gauge measures right now, on top of the predicted tide.",
  },
  {
    title: "Ask: does it reach this address?",
    body: "USGS elevation says how high the water must rise before it can flow to this exact address from the sea, and how deep it would stand. Two homes on the same block can get different answers.",
  },
  {
    title: "Tell people and their agents",
    body: "Watch, get ready or leave now, only when the level changes. Email or text for the person, a signed webhook and MCP tools for their personal agent.",
  },
];

export function Pipeline() {
  return (
    <ol className="pipeline">
      {STEPS.map((s, i) => (
        <li key={s.title}><span className="step num">{i + 1}</span><h3>{s.title}</h3><p>{s.body}</p></li>
      ))}
      <li className="pipeline-mcp">
        <h3>Data, all public, no keys</h3>
        <ul className="tools num">
          {["NHC ATCF advisories", "NOAA CO-OPS gauges", "USGS 3DEP elevation", "NWS coastal alerts", "Open-Meteo"].map((t) => <li key={t}>{t}</li>)}
        </ul>
      </li>
    </ol>
  );
}

export function Findings({ replay }) {
  const s = replay?.summary;
  const zSandy = s?.zones?.sandy_2012?.evacuate_in_time;
  const rSandy = s?.rules?.sandy_2012?.evacuate_in_time;
  const zIrma = s?.zones?.irma_2017?.needless_evacuation_rate;
  const rIrma = s?.rules?.irma_2017?.needless_evacuation_rate;
  const cal = replay?.calibration;
  const learn = replay?.learn;
  const items = [
    {
      figure: zSandy != null ? `${pct(zSandy, 0)} → ${pct(rSandy, 0)}` : "–",
      caption: "Sandy homes that flooded and were told to leave in time: zones, then address rules",
      title: "Sandy was “only” a Category 1",
      body: "Zone maps order evacuations by category, so a Cat 1 empties only the lowest zone. Sandy's surge came from its size and a left hook into the harbour at high tide, and flooded homes far above Zone A. Forecasting the water at each address, not the storm's category, is what caught them.",
    },
    {
      figure: rIrma != null ? `${pct(rIrma, 0)}` : "–",
      caption: "of low-lying Miami homes that stayed dry were still told to leave (address rules)",
      title: "Irma is where every policy over-warns",
      body: `Two days out, official forecasts aimed a Category 4 at Miami; Irma tracked up the west coast instead. Rules that trust the forecast evacuate homes that stay dry${zIrma != null ? `, and the zone policy did no better (${pct(zIrma, 0)})` : ""}. This is the judgment call the LLM policy is there to make better.`,
    },
    {
      figure: cal ? `${pct(cal.train.needless_evacuation_rate, 0)}` : "–",
      caption: "needless evacuations on the train storms with the fitted thresholds",
      title: "A wider “worst case” bought nothing",
      body: "We expected that deciding on an inflated high scenario (the forecast surge times 1.6, like NHC's 10% exceedance) would catch more floods. Fitted on Sandy and Matthew, it only evacuated more dry homes; the expected forecast with a 24-hour trigger was already early enough. The high scenario now informs the messages and the LLM, not the rules.",
    },
    learn && {
      figure: `${learn.held_out_rules.alerts_per_household.toFixed(1)} → ${learn.held_out_after.alerts_per_household.toFixed(1)}`,
      caption: "alerts per household on held-out storms: address rules, then the LLM with its own lessons",
      title: "The LLM said the same with a third of the messages",
      body: `On the same ${learn.held_out_after.flooded_n + learn.held_out_after.spared_n + learn.held_out_after.high_n} held-out households, the LLM evacuated every flooded home in time, like the rules, but held its level steady instead of stepping up and down with each advisory. Then it replayed Sandy and Matthew, read its own mistakes and wrote the lessons below; scored on Irma and Isabel, which it never saw, they pushed evacuations earlier (median lead ${hrs(learn.held_out_before.median_evacuate_lead_h)} → ${hrs(learn.held_out_after.median_evacuate_lead_h)}) but did not cut needless evacuations, which stay at ${pct(learn.held_out_after.needless_evacuation_rate, 0)} for every policy on that sample. A small sample (${(learn.tokens / 1000).toFixed(0)}k gateway tokens); the loop is built to run on many more storms.`,
    },
  ].filter(Boolean);
  return (
    <div className="findings">
      {items.map((f) => (
        <article key={f.title} className="finding">
          <div className="finding-figure"><span className="num">{f.figure}</span><small>{f.caption}</small></div>
          <div><h3>{f.title}</h3><p>{f.body}</p></div>
        </article>
      ))}
      {learn?.lessons && (
        <div className="lessons">
          <h3>Lessons it wrote for itself</h3>
          <ul>{learn.lessons.split("\n").filter((l) => l.trim()).map((l) => <li key={l}>{l.replace(/^[-*]\s*/, "")}</li>)}</ul>
        </div>
      )}
      <p className="caveat">
        Caveats: ground truth is a connected-bathtub reconstruction from tide gauges and USGS elevation, which ignores
        levees, drainage and waves, and a gauge that fails at the peak (Sandy Hook during Sandy) drops out. The surge
        model is parametric, not SLOSH or ADCIRC: it reproduces Sandy, Matthew and Irma within a foot or two at the peak and
        overestimates Isabel at Norfolk by about 2×. Four storms is a small sample.
      </p>
    </div>
  );
}

const STACK = [
  { key: "learn", name: "Learning loop", file: "agent/evals/surge/learn.py", code: LEARN,
    body: "Replays the train storms with the LLM policy, collects its mistakes, and has the model distil them into lessons that go into its own instructions. Runs locally or in a Chalk sandbox." },
  { key: "evals", name: "Evaluations", file: "agent/evals/surge/chalk_eval.py", code: CHALK_EVAL,
    body: "One dataset of households across four storms, one task per policy, five deterministic scorers, in the surgetrace-sentinel suite. Every run is pinned to a git SHA." },
  { key: "gateway", name: "Model Gateway", file: "agent/surge_sentinel/policy.py", code: GATEWAY,
    body: "The LLM policy and the teacher call models through the gateway's OpenAI-compatible router. The model is only asked when the forecast for an address changes level." },
];

export function ChalkStack() {
  const [k, setK] = useState("learn");
  const cur = STACK.find((s) => s.key === k);
  return (
    <div className="stack">
      <div className="stack-list" role="tablist" aria-label="Chalk components">
        {STACK.map((s) => (
          <button key={s.key} role="tab" aria-selected={k === s.key} className={k === s.key ? "on" : ""} onClick={() => setK(s.key)}>
            <span className="stack-name">{s.name}</span><span className="stack-body">{s.body}</span>
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
