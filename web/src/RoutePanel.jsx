import { useEffect, useState } from "react";
import { Segmented } from "./shared.jsx";

// Fire-safe routing: pick A and B on the map, the sentinel's /api/route returns
// one path that passes every road before the fire, or "no safe path".

// Closed-road colors by restriction code (fire and surge share the router; see agent/routing).
export const CLOSED_COLOR = {
  perimeter: "#ff3b1f", hotspot: "#ffb000", arrival: "#c63a1d", closure: "#e0245e",
  flood: "#2f8fd8", bridge: "#7fb2e5", tunnel: "#4a6fa5",
};
export const BLOCKING_COLOR = "#b57bff";   // closed stretches of the fastest route
const CLOSED_LABEL = {
  perimeter: "Near a fire perimeter", hotspot: "Near a hot spot", closure: "Road closed (Caltrans)",
  flood: "Flooded road", bridge: "Bridge closed (wind)", tunnel: "Tunnel closed (flooding)",
};
const ARRIVAL_LABEL = { fire: "Fire arrives first", surge: "Water arrives first" };
const RISKS = [["cautious", "Cautious"], ["balanced", "Balanced"], ["fastest", "Fastest"]];
const KINDS = [["route", "A → B"], ["escape", "Escape"]];
// Escape keeps a smaller fire margin: near the fire, leaving close to the front is still leaving.
const MARGINS = {
  route: [[1, "1 h"], [2, "2 h"], [3, "3 h"], [4, "4 h"]],
  escape: [[0.5, "30 min"], [1, "1 h"], [2, "2 h"]],
};
const LEAVE_IN = [[0, "Now"], [1, "+1 h"], [3, "+3 h"], [6, "+6 h"]];

export function useSafeRoute({ mode, departHour, hazard = "fire" }) {
  const [kind, setKindRaw] = useState("route");       // "route" (A -> B) | "escape" (A only)
  const [picking, setPicking] = useState(null);       // null | "from" | "to"
  const [from, setFrom] = useState(null);             // [lat, lon]
  const [to, setTo] = useState(null);
  const [risk, setRisk] = useState("balanced");
  // Escape margins: 30 min ahead of a fire front, 1 h ahead of rising water.
  const [margins, setMargins] = useState({ route: 2, escape: hazard === "surge" ? 1 : 0.5 });
  const [leaveIn, setLeaveIn] = useState(0);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const depart = mode === "live" ? leaveIn : departHour;
  const margin = margins[kind];
  const setMargin = (m) => setMargins((ms) => ({ ...ms, [kind]: m }));
  const setKind = (k) => {
    if (k === kind) return;
    setKindRaw(k); setTo(null); setResult(null); setError(null);
    setPicking(from ? (k === "route" ? "to" : null) : picking && "from");
  };

  useEffect(() => {
    if (!from || (kind === "route" && !to)) return;
    const ctrl = new AbortController();
    // Debounced so scrubbing the replay timeline doesn't fire a request per hour.
    const t = setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const r = await fetch("/api/route", {
          method: "POST", headers: { "Content-Type": "application/json" }, signal: ctrl.signal,
          body: JSON.stringify({ from, to: kind === "route" ? to : null, hazard, mode, depart_hour: depart,
                                 margin_hours: margin, risk }),
        });
        const body = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
        if (!body) throw new Error("unreachable");
        setResult(body);
      } catch (e) {
        if (e.name !== "AbortError") { setResult(null); setError("unreachable"); }
      } finally {
        if (!ctrl.signal.aborted) setLoading(false);
      }
    }, 350);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [kind, from, to, hazard, mode, depart, margin, risk]);

  const onMapPick = ({ lng, lat }) => {
    const p = [+lat.toFixed(5), +lng.toFixed(5)];
    if (picking === "from") { setFrom(p); setTo(null); setResult(null); setPicking(kind === "route" ? "to" : null); }
    else if (picking === "to") { setTo(p); setPicking(null); }
  };
  const start = () => { setFrom(null); setTo(null); setResult(null); setError(null); setPicking("from"); };
  const clear = () => { setFrom(null); setTo(null); setResult(null); setError(null); setPicking(null); };

  return { kind, setKind, picking, from, to, risk, setRisk, margin, setMargin, leaveIn, setLeaveIn, result,
           loading, error, onMapPick, start, clear, mode, hazard };
}

const fmtTime = (iso, live) => iso && new Date(iso).toLocaleString("en-US", {
  ...(live ? { timeZone: "America/Los_Angeles" } : {}), weekday: "short", hour: "numeric", minute: "2-digit",
});

export function RouteCard({ route, hourLabel }) {
  const { picking, from, to, loading, error, mode } = route;
  const live = mode === "live";
  const surge = route.hazard === "surge";
  const escape = route.kind === "escape";
  // Drop a response that belongs to the other mode (a request still in flight when the toggle flipped).
  const result = route.result && (route.result.kind === "escape") === escape ? route.result : null;
  return (
    <div className="card route-card">
      <div className="card-head">
        <h3>{escape ? "Escape route" : "Safe route"}</h3>
        <span className="muted">{live ? "live feeds" : hourLabel}{loading && <span className="spinner route-spin" />}</span>
      </div>
      <Segmented label="Route type" value={route.kind} onChange={route.setKind} options={KINDS} />

      {!from && !picking && (
        <>
          <p className="small-p muted route-intro">
            {escape
              ? `Drop one point. Find out whether you can get out ahead of the ${surge ? "water" : "fire"}, the fastest way to safety, and the latest time you can still leave.`
              : surge
                ? `One path from A to B that passes every road at least ${route.margin} h before the water, avoiding flooded roads, bridges in storm winds and flooded tunnels.`
                : `One path from A to B that passes every road at least ${route.margin} h before the fire, away from perimeters and fresh hot spots.`}
          </p>
          <button className="route-btn" onClick={route.start}>{escape ? "Pick your location" : "Pick start and destination"}</button>
        </>
      )}
      {picking && (
        <p className="route-picking" role="status">
          <span className="route-pin">{picking === "from" ? "A" : "B"}</span>
          Click the map to set {escape ? "your location" : picking === "from" ? "the start" : "the destination"}
          <button className="route-link" onClick={route.clear}>Cancel</button>
        </p>
      )}
      {from && (
        <div className="route-points num">
          <span><i className="route-pin">A</i>{from[0].toFixed(4)}, {from[1].toFixed(4)}</span>
          {to && !escape && <span><i className="route-pin b">B</i>{to[0].toFixed(4)}, {to[1].toFixed(4)}</span>}
          {!picking && <button className="route-link" onClick={route.start}>{escape ? "New location" : "New route"}</button>}
        </div>
      )}

      <div className="route-controls">
        <label><span>Risk</span><Segmented label="Risk tolerance" value={route.risk} onChange={route.setRisk} options={RISKS} /></label>
        <label><span>Margin</span><Segmented label="Safety margin" value={route.margin} onChange={route.setMargin} options={MARGINS[route.kind]} /></label>
        {live && <label><span>Leave</span><Segmented label="Departure" value={route.leaveIn} onChange={route.setLeaveIn} options={LEAVE_IN} /></label>}
      </div>
      {!live && from && <p className="hint">Scrub the timeline to change the departure hour.</p>}

      {error && (
        <p className="route-alert">Route API unreachable. In <code>agent/</code> run <code>python -m sentinel mcp --http 8765</code>.</p>
      )}
      {result?.status === "error" && <p className="route-alert">{result.message}</p>}

      {!escape && result?.status === "no_safe_path" && (
        <div className="route-result">
          <div className="route-verdict bad">No safe path</div>
          <p className="small-p">{result.message}</p>
          <Blocked items={result.blocked_by} title="What closes the way" />
        </div>
      )}

      {escape && result && result.status !== "error" && <EscapeResult result={result} live={live} />}

      {!escape && result?.status === "ok" && (
        <div className="route-result">
          <div className="route-verdict">
            <b className="num">{result.eta_minutes}<small>min</small></b>
            <b className="num">{result.distance_mi}<small>mi</small></b>
            <span>arrive {fmtTime(result.arrive_time, live)}</span>
          </div>
          <dl className="facts">
            <div><dt>Leave</dt><dd className="num">{fmtTime(result.depart_time, live)}</dd></div>
            <div><dt>Smallest lead over the fire</dt><dd className="num">{result.min_margin_hours != null ? `${result.min_margin_hours} h` : "Fire never reaches it"}</dd></div>
            <div><dt>Detour vs fastest route</dt><dd className="num">{result.detour_minutes ? `+${result.detour_minutes} min` : "None"}</dd></div>
          </dl>
          {result.warnings?.length > 0 && (
            <ul className="route-warn">{result.warnings.slice(0, 4).map((w) => <li key={w}>{w}</li>)}</ul>
          )}
          <Blocked items={result.avoided} title="Avoided" />
          <Directions steps={result.directions} live={live} hazard={result.hazard} mapsUrl={result.google_maps_url} />
        </div>
      )}

      {result && result.status !== "error" && (
        <>
          <div className="route-key">
            <span><i className="lg-path" />Route</span>
            {escape && <span><i className="route-pin safe">S</i>Safe zone reached</span>}
            <span><i className="lg-closed blocking" style={{ "--c": BLOCKING_COLOR }} />Blocks the fastest route</span>
            {[...new Set((result.closed_at_departure?.features ?? []).map((f) => f.properties.restriction))].map((k) => (
              <span key={k}><i className="lg-closed" style={{ "--c": CLOSED_COLOR[k] ?? CLOSED_COLOR.arrival }} />
                {k === "arrival" ? ARRIVAL_LABEL[result.hazard] ?? "Hazard arrives first" : CLOSED_LABEL[k] ?? k}</span>
            ))}
          </div>
          <p className="hint">{result.notes?.join(" ")}</p>
        </>
      )}
    </div>
  );
}

function EscapeResult({ result, live }) {
  if (result.status === "already_safe") {
    return (
      <div className="route-result">
        <div className="route-verdict escape safe">Already safe</div>
        <p className="small-p">{result.message}</p>
      </div>
    );
  }
  if (result.status === "no_escape") {
    return (
      <div className="route-result">
        <div className="route-verdict escape bad">No escape route</div>
        <p className="small-p">{result.message}</p>
        <Blocked items={result.blocked_by} title="What closes the way" />
      </div>
    );
  }
  return (
    <div className="route-result">
      <div className={`route-verdict escape ${result.tight ? "tight" : "good"}`}>
        {result.tight ? "You can get out, barely" : "You can get out"}
      </div>
      <div className="route-verdict">
        <b className="num">{result.eta_minutes}<small>min to safety</small></b>
        <b className="num">{result.distance_mi}<small>mi</small></b>
      </div>
      <dl className="facts">
        <div><dt>Leave by</dt><dd className="num">{result.leave_by_time ? fmtTime(result.leave_by_time, live) : "No deadline in the next 24 h"}</dd></div>
        <div><dt>Leaving</dt><dd className="num">{fmtTime(result.depart_time, live)}</dd></div>
        <div><dt>Safe by</dt><dd className="num">{fmtTime(result.arrive_time, live)}</dd></div>
        <div><dt>Smallest lead over the fire</dt><dd className="num">{result.min_margin_hours != null ? `${result.min_margin_hours} h` : "Fire never reaches it"}</dd></div>
      </dl>
      {result.warnings?.length > 0 && (
        <ul className="route-warn">{result.warnings.slice(0, 4).map((w) => <li key={w}>{w}</li>)}</ul>
      )}
      <Blocked items={result.avoided} title="Avoided" />
      <Directions steps={result.directions} live={live} hazard={result.hazard} mapsUrl={result.google_maps_url} />
    </div>
  );
}

const MANEUVER_ICON = {
  depart: "↑", straight: "↑", "slight-left": "↖", "slight-right": "↗", "turn-left": "↰", "turn-right": "↱",
  "sharp-left": "↙", "sharp-right": "↘", uturn: "↶", ramp: "↗", "ramp-left": "↖", "ramp-right": "↗",
  merge: "⤴", arrive: "●",
};

function Directions({ steps, live, hazard, mapsUrl }) {
  const [open, setOpen] = useState(false);
  if (!steps?.length) return null;
  const turns = steps.length - 1;
  const hazardNoun = hazard === "surge" ? "water" : "fire";
  return (
    <div className="route-directions">
      <button className="route-dir-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span>Directions</span>
        <span className="num muted">{turns} step{turns === 1 ? "" : "s"}</span>
        <svg className="chev" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="currentColor" strokeWidth="1.4" /></svg>
      </button>
      {open && (
        <ol className="route-steps">
          {steps.map((s, i) => (
            <li key={i} className={s.maneuver === "arrive" ? "arrive" : ""}>
              <span className="step-icon" aria-hidden="true">{MANEUVER_ICON[s.maneuver] ?? "↑"}</span>
              <div>
                <div className="step-text">{s.instruction}</div>
                <div className="step-meta num">
                  {s.maneuver === "arrive" ? fmtTime(s.at_time, live) : `${s.mi} mi · ${Math.max(1, Math.round(s.minutes))} min`}
                  {s.lead_h != null && s.lead_h < 6 && <span className="step-lead"> · {hazardNoun} in {s.lead_h} h</span>}
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
      {open && mapsUrl && (
        <a className="route-maps-link" href={mapsUrl} target="_blank" rel="noreferrer">
          Open this route in Google Maps
        </a>
      )}
    </div>
  );
}

function Blocked({ items, title }) {
  if (!items?.length) return null;
  return (
    <>
      <div className="group-title">{title}</div>
      <ul className="route-avoided" style={{ "--c": BLOCKING_COLOR }}>
        {items.map((a) => <li key={a.road}><b>{a.road}</b><span>{a.reason}</span></li>)}
      </ul>
    </>
  );
}
