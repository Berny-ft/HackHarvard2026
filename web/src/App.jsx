import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import "mapbox-gl/dist/mapbox-gl.css";
import "./styles.css";
import MapView, { BASEMAPS, CAMERAS, LIGHTS } from "./MapView.jsx";
import { loadArrival } from "./fire.js";
import { loadBuildings } from "./buildings.js";
import { Chart } from "./Charts.jsx";
import { LEVEL_COLOR, LEVEL_LABEL, Logo, navigate, pct } from "./shared.jsx";

const INCIDENTS = [
  ["live", "Live"],
  ["eaton_2025", "Eaton 2025"],
  ["palisades_2025", "Palisades 2025"],
  ["woolsey_2018", "Woolsey 2018"],
  ["mountain_2024", "Mountain 2024"],
  ["franklin_2024", "Franklin 2024"],
];

const LIVE_LAYERS = [
  ["scan", "Sentinel alert areas", "var(--ember)"],
  ["redFlags", "Red Flag Warnings", "#e0245e"],
  ["santaAna", "Santa Ana forecast, 48 h", "#d9482b"],
  ["activeFires", "Active fires (NIFC)", "#ff3b1f"],
  ["hotspots", "Satellite hot spots", "#ffb000"],
  ["powerLines", "Power lines", "#ffd23f"],
  ["wind", "Wind", "#9fb7d6"],
];
const INCIDENT_LAYERS = [
  ["households", "Sentinel households", "var(--signal)"],
  ["spread", "Modeled spread", "#e5482a"],
  ["smoke", "Flames, embers & smoke (3D)", "#b9b2a8"],
  ["buildings", "Buildings (3D, burn on arrival)", "#d8c8ad"],
  ["real", "Final perimeter (CAL FIRE)", "#ffffff"],
  ["structures", "Structures reached", "#b3261e"],
  ["detections", "Satellite detections", "#ffb000"],
  ["wind", "Wind", "#9fb7d6"],
];
const TERRAIN_LAYERS = [
  ["relief", "Relief shading & contours", "#a8957a"],
];
const HISTORY_LAYERS = [
  ["risk", "10-year burn likelihood", "#f08a2c"],
  ["timesBurned", "Times burned since 1950", "#e8873a"],
  ["history", "Fire perimeters, 1950–2025", "#f3d38a"],
];

const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const compass = (deg) => COMPASS[Math.round(deg / 22.5) % 16];
const LEVELS = ["none", "watch", "warn", "evacuate"];
const PT = { timeZone: "America/Los_Angeles" };

async function loadIncident(key) {
  const get = (f) => fetch(`/data/incidents/${key}/${f}`).then((r) => r.json());
  const [info, series, arrival, real, structures, detections, buildings] = await Promise.all([
    get("incident.json"), get("timeseries.json"), loadArrival(`/data/incidents/${key}`),
    get("real_perimeter.geojson"),
    get("structures.geojson").catch(() => ({ type: "FeatureCollection", features: [] })),
    get("detections.geojson").catch(() => ({ type: "FeatureCollection", features: [] })),
    loadBuildings(`/data/incidents/${key}`).catch(() => null),
  ]);
  return { info, series, arrival, real, structures, detections, buildings };
}

// Alert demo: move a fire's geometry by (dlon, dlat) so it burns toward the watched address.
const shiftCoords = (c, dx, dy) => (typeof c[0] === "number" ? [c[0] + dx, c[1] + dy] : c.map((x) => shiftCoords(x, dx, dy)));
const shiftFc = (fc, dx, dy) => ({
  ...fc,
  features: fc.features.map((f) => ({ ...f, geometry: f.geometry && { ...f.geometry, coordinates: shiftCoords(f.geometry.coordinates, dx, dy) } })),
});
function shiftData(d, dx, dy) {
  return {
    ...d,
    info: { ...d.info, ignition: { ...d.info.ignition, lon: d.info.ignition.lon + dx, lat: d.info.ignition.lat + dy } },
    shift: [dx, dy], real: shiftFc(d.real, dx, dy),
    structures: shiftFc(d.structures, dx, dy), detections: shiftFc(d.detections, dx, dy),
  };
}
const EMPTY_FC = { type: "FeatureCollection", features: [] };
const api = (path, body) => fetch(`/api/demo/${path}`, body === undefined ? undefined : {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
}).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });

// Places that needed a warning: destroyed homes, or burned points on fires without damage records.
const needed = (h) => h.category === "destroyed" || h.category === "burned";

const levelAt = (alerts, hour) => {
  let lvl = "none";
  for (const a of alerts) if (a.hour <= hour) lvl = a.level;
  return lvl;
};

export default function App() {
  const [incident, setIncident] = useState(() => new URLSearchParams(window.location.search).get("fire") || "live");
  const [live, setLive] = useState(null);
  const [data, setData] = useState(null);
  const [hour, setHour] = useState(12);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [patterns, setPatterns] = useState(null);
  const [risk, setRisk] = useState(null);
  const [replay, setReplay] = useState(null);
  const [selected, setSelected] = useState(null);
  const [focus, setFocus] = useState(null);
  const [layersOpen, setLayersOpen] = useState(() => window.innerWidth > 900);
  const [layers, setLayers] = useState({
    spread: true, smoke: true, buildings: true, relief: true, real: true, structures: false, detections: true, households: true,
    risk: false, timesBurned: false, history: false,
    wind: true, redFlags: true, santaAna: true, activeFires: true, hotspots: true, powerLines: false, scan: true,
  });
  const isLive = incident === "live";
  const [basemap, setBasemap] = useState("standard");
  const [light, setLight] = useState("dusk");
  const [camera, setCamera] = useState("3d");
  const [demo, setDemo] = useState(null);          // demo-server config while the alert demo runs
  const [demoState, setDemoState] = useState(null);
  const [demoError, setDemoError] = useState(null);
  const demoRef = useRef(null);
  demoRef.current = demo;

  useEffect(() => {
    document.title = "Trace wildfire map";
    const json = (p) => fetch(p).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    json("/data/patterns.json").then(setPatterns);
    json("/data/risk_model_metrics.json").then(setRisk);
    json("/data/sentinel/replay.json").then(setReplay);
    Promise.all(["status.json", "weather.json", "red_flags.geojson", "air_quality.json", "incidents.json"]
      .map((f) => json(`/data/live/${f}`)))
      .then(([status, weather, redFlags, air, incidents]) => setLive({ status, weather, redFlags, air, incidents }));
  }, []);

  useEffect(() => {
    setData(null);
    setPlaying(false);
    setSelected(null);
    const url = new URL(window.location.href);
    if (incident === "live") url.searchParams.delete("fire"); else url.searchParams.set("fire", incident);
    window.history.replaceState({}, "", url);
    if (demoRef.current && demoRef.current.incident !== incident) setDemo(null);
    if (incident === "live") return;
    loadIncident(incident).then((d) => { setData(d); setHour(demoRef.current ? 0 : 12); });
  }, [incident]);

  const startDemo = async () => {
    try {
      const cfg = await api(`config?incident=${incident}`);
      setDemoState(await api("reset", {}));
      setDemoError(null);
      setSelected(null);
      setPlaying(false);
      setDemo(cfg);
      if (incident !== cfg.incident) setIncident(cfg.incident); else setHour(0);
    } catch {
      setDemoError("Start the demo server first (in agent/: .venv/bin/python -m sentinel demo-server), and make sure this fire has a replay.");
    }
  };
  const stopDemo = () => { setDemo(null); setDemoState(null); setPlaying(false); };

  // Every hour the map shows, the sentinel decides for the watched address; emails go out server-side.
  useEffect(() => {
    if (!demo || !data) return;
    api("step", { hour }).then(setDemoState).catch(() => setDemoError("Lost the demo server."));
  }, [hour, demo, data]);

  // Email delivery happens in the background; poll so each alert's status updates.
  useEffect(() => {
    if (!demo) return;
    const id = setInterval(() => api("state").then(setDemoState).catch(() => {}), 1000);
    return () => clearInterval(id);
  }, [demo]);

  const viewData = useMemo(() => (demo && data ? shiftData(data, demo.dlon, demo.dlat) : data), [data, demo]);
  const demoHome = demo && { lon: demo.lon, lat: demo.lat, level: demoState?.now?.level ?? "none", label: "Your address" };

  useEffect(() => {
    if (!playing || !data) return;
    const id = setInterval(() => {
      setHour((h) => {
        if (h >= data.info.hours) { setPlaying(false); return h; }
        return h + 1;
      });
    }, 420 / speed);
    return () => clearInterval(id);
  }, [playing, speed, data]);

  // Space toggles playback, arrows step an hour.
  useEffect(() => {
    if (!data) return;
    const on = (e) => {
      if (e.target.closest("input, select, textarea, button")) return;
      if (e.code === "Space") { e.preventDefault(); togglePlay(); }
      if (e.key === "ArrowRight") { setPlaying(false); setHour((h) => Math.min(data.info.hours, h + 1)); }
      if (e.key === "ArrowLeft") { setPlaying(false); setHour((h) => Math.max(0, h - 1)); }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  });

  const togglePlay = () => {
    if (data && hour >= data.info.hours) setHour(0);
    setPlaying((p) => !p);
  };

  const now = data?.series[Math.min(hour, data.series.length - 1)];
  const reached = useMemo(() => {
    if (!data) return null;
    let n = 0, destroyed = 0;
    for (const f of data.structures.features) {
      const t = f.properties.sim_arrival_hr;
      if (t != null && +t <= hour) {
        n++;
        if (f.properties.DAMAGE.startsWith("Destroyed")) destroyed++;
      }
    }
    return { n, destroyed };
  }, [data, hour]);

  const homes = useMemo(() => (replay?.households ?? [])
    .map((h, idx) => ({ ...h, idx }))
    .filter((h) => h.incident === incident), [replay, incident]);

  const homesGeo = useMemo(() => ({
    type: "FeatureCollection",
    features: homes.map((h) => ({
      type: "Feature", geometry: { type: "Point", coordinates: [h.lon, h.lat] },
      properties: { idx: h.idx, level: levelAt(h.alerts, hour), destroyed: needed(h),
                    selected: h.idx === selected },
    })),
  }), [homes, hour, selected]);

  const clock = now && new Date(now.time).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const toggle = (k) => setLayers((l) => ({ ...l, [k]: !l[k] }));
  const selectedHome = selected != null ? replay?.households[selected] : null;

  return (
    <div className={`app ${isLive ? "is-live" : "is-incident"}`}>
      <header className="topbar">
        <a href="/" onClick={navigate} className="topbar-logo" aria-label="Trace home"><Logo /></a>
        <nav className="hazard-switch" aria-label="Hazard">
          <a href="/map" className="on" aria-current="page">Fire</a>
          <a href="/surge/map" onClick={navigate}>Surge</a>
        </nav>
        <nav className="incident-tabs" aria-label="Choose a view">
          {INCIDENTS.map(([k, label]) => (
            <button key={k} className={k === incident ? "on" : ""} aria-pressed={k === incident} onClick={() => setIncident(k)}>
              {k === "live" && <span className="live-dot" aria-hidden="true" />}{label}
            </button>
          ))}
        </nav>
        {!isLive && (demo
          ? <button className="demo-btn on" onClick={stopDemo}>Exit alert demo</button>
          : <button className="demo-btn" onClick={startDemo} title="Replay this fire toward your address and get real alert emails">
              <span className="demo-dot" aria-hidden="true" />Alert demo
            </button>)}
        <span className="topbar-spacer" />
        <span className="topbar-meta">
          {isLive && live?.status && (
            <>Feeds updated <b className="num">{new Date(live.status.updated).toLocaleString("en-US", { ...PT, month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} PT</b></>
          )}
          {!isLive && data && <>Los Angeles County, replayed with what was known at each hour</>}
        </span>
      </header>

      <main className="body">
        <section className="mapwrap">
          <MapView incident={incident} data={viewData} hour={hour} playRate={playing ? speed / 0.42 : 0} layers={layers}
                   households={demo ? EMPTY_FC : homesGeo} home={demoHome}
                   basemap={basemap} light={light} camera={camera} focus={focus}
                   onSelectHousehold={setSelected} />

          <div className={`panel layers-panel ${layersOpen ? "open" : ""}`}>
            <button className="panel-toggle" onClick={() => setLayersOpen((o) => !o)} aria-expanded={layersOpen}>
              <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M8 1.5 15 5.2 8 9 1 5.2Z M1.8 8.4 8 11.8l6.2-3.4M1.8 11.4 8 14.8l6.2-3.4" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
              </svg>
              Layers
              <svg className="chev" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="currentColor" strokeWidth="1.4" /></svg>
            </button>
            <div className="layers-body">
              <div className="layers-inner">
                <LayerGroup title={isLive ? "Now" : "This fire"} items={isLive ? LIVE_LAYERS : INCIDENT_LAYERS} layers={layers} toggle={toggle} />
                <LayerGroup title="Terrain" items={TERRAIN_LAYERS} layers={layers} toggle={toggle} />
                <LayerGroup title="History" items={HISTORY_LAYERS} layers={layers} toggle={toggle} />
              </div>
            </div>
          </div>

          <div className="viewbar">
            <Segmented label="Basemap" value={basemap} onChange={setBasemap}
                       options={Object.entries(BASEMAPS).map(([k, v]) => [k, v.label])} />
            {BASEMAPS[basemap].standard && (
              <Segmented label="Light" value={light} onChange={setLight}
                         options={LIGHTS.map((l) => [l, l[0].toUpperCase() + l.slice(1)])} />
            )}
            <Segmented label="Camera" value={camera} onChange={setCamera}
                       options={Object.entries(CAMERAS).map(([k, v]) => [k, v.label])} />
          </div>

          {!isLive && !data && <div className="loading"><span className="spinner" />Loading the {INCIDENTS.find(([k]) => k === incident)[1]} replay</div>}
          {isLive && <LiveBanner live={live} />}
          {demoError && <div className="banner demo-error" role="alert"><b>Alert demo</b><span>{demoError}</span>
            <button onClick={() => setDemoError(null)} aria-label="Dismiss">×</button></div>}
          <Legend live={isLive} />
        </section>

        <aside className="side">
          {isLive ? (
            <LiveSide live={live} onFocus={setFocus} patterns={patterns} risk={risk} />
          ) : demo ? (
            <DemoCard demo={demo} state={demoState} hour={hour} />
          ) : selectedHome ? (
            <HouseholdCard home={selectedHome} hour={hour} onClose={() => setSelected(null)}
                           onJump={(h) => { setPlaying(false); setHour(h); }} />
          ) : (
            data && now && (
              <>
                <ReplayCard homes={homes} hour={hour} />
                <div className="card">
                  <div className="card-head"><h3>Fire behavior</h3><span className="num muted">{clock}</span></div>
                  <div className="metrics">
                    <Metric v={now.wind_mph} u="mph" l={`Wind from ${compass(now.wind_dir_deg)}`} />
                    <Metric v={now.gust_mph} u="mph" l="Gusts" hot={now.gust_mph >= 50} />
                    <Metric v={now.rh_pct} u="%" l="Humidity" hot={now.rh_pct < 15} />
                    <Metric v={Math.round(now.temp_f)} u="°F" l="Temperature" />
                    <Metric v={now.fuel_moisture_1h_pct} u="%" l="1-hour fuel moisture" hot={now.fuel_moisture_1h_pct < 5} />
                    <Metric v={Math.round(now.growth_acres_per_hr).toLocaleString()} u="ac/h" l="Modeled growth" />
                  </div>
                </div>
                <IncidentCard data={data} now={now} reached={reached} />
              </>
            )
          )}
        </aside>
      </main>

      {!isLive && (
        <footer className="timeline">
          <div className="controls">
            <button className="play" onClick={togglePlay} disabled={!data} aria-label={playing ? "Pause" : "Play"}>
              {playing
                ? <svg width="12" height="12" viewBox="0 0 12 12"><rect x="2" y="1.5" width="3" height="9" rx="1" /><rect x="7" y="1.5" width="3" height="9" rx="1" /></svg>
                : <svg width="12" height="12" viewBox="0 0 12 12"><path d="M3 1.6v8.8a.6.6 0 0 0 .9.5l7-4.4a.6.6 0 0 0 0-1L3.9 1.1a.6.6 0 0 0-.9.5Z" /></svg>}
            </button>
            <SpeedControl speed={speed} onChange={setSpeed} />
            <Scrubber hour={hour} max={data?.info.hours ?? 72} start={data?.info.start}
                      onChange={(h) => { setPlaying(false); setHour(h); }} />
            <div className="clock">
              <span className="num">T+{hour}h</span>
              <small className="num">{clock ?? "–"}</small>
            </div>
          </div>
          {data && (
            <div className="charts">
              <Chart title="Fire area" unit="acres" rows={data.series} hour={hour}
                     series={[{ key: "acres", color: "#ff6a2b", fill: true, label: "Model" },
                              ...(data.series[0].observed_acres != null ? [{ key: "observed_acres", color: "#ffb547", label: "Satellite" }] : [])]} />
              <Chart title="Growth" unit="ac/h" rows={data.series} hour={hour}
                     series={[{ key: "growth_acres_per_hr", color: "#f08a2c", fill: true }]} />
              <Chart title="Wind" unit="mph" rows={data.series} hour={hour}
                     series={[{ key: "wind_mph", color: "#8fb3dc", fill: true, label: "Sustained" }, { key: "gust_mph", color: "#c7d9ee", label: "Gust" }]} />
              <Chart title="1-hour fuel moisture" unit="%" rows={data.series} hour={hour}
                     series={[{ key: "fuel_moisture_1h_pct", color: "#bade61", fill: true }]} />
            </div>
          )}
        </footer>
      )}
    </div>
  );
}

// The phone call the evacuate alert placed: the agent's opening line, then each question it brought to the sentinel.
function CallLog({ call }) {
  return (
    <div className="call-log">
      <div className="call-head">
        <b>Phone call</b>
        <span className="num muted">{call.phone} · {call.status === "placed" ? "connected" : call.status}</span>
      </div>
      <ol>
        {call.opening && !/^(failed|skipped)/.test(call.status) && <li className="agent"><span>Agent</span><p>{call.opening}</p></li>}
        {call.turns.map((t, i) => (
          <Fragment key={i}>
            <li className="caller"><span>Caller</span><p>{t.said}</p></li>
            <li className="agent"><span>Sentinel</span><p>{t.reply}</p></li>
          </Fragment>
        ))}
        {call.status === "placed" && call.turns.length === 0 &&
          <li className="hh-empty">Pick up and ask why, when to leave, or which way to go.</li>}
      </ol>
    </div>
  );
}

// Playback speed, 0.1× to 4×, on a log scale so the slow end gets as much travel as the fast end.
// 1× is one modeled hour every 0.42 s; the fire itself animates smoothly between hours at any speed.
const SPEED_MIN = 0.1, SPEED_MAX = 4, SPEED_STEPS = 60;
const SPEED_PRESETS = [0.1, 0.25, 0.5, 1, 2, 4];
const toStep = (v) => Math.round((Math.log(v / SPEED_MIN) / Math.log(SPEED_MAX / SPEED_MIN)) * SPEED_STEPS);
const fromStep = (i) => {
  const v = SPEED_MIN * (SPEED_MAX / SPEED_MIN) ** (i / SPEED_STEPS);
  return SPEED_PRESETS.find((p) => Math.abs(v / p - 1) < 0.05) ?? +v.toPrecision(2);   // land exactly on 0.5×, 1×…
};
const speedLabel = (v) => `${v < 1 ? +v.toFixed(2) : +v.toFixed(1)}×`;

function SpeedControl({ speed, onChange }) {
  return (
    <div className="speed" role="group" aria-label="Playback speed">
      <input type="range" min={0} max={SPEED_STEPS} step={1} value={toStep(speed)}
             onChange={(e) => onChange(fromStep(+e.target.value))}
             aria-label="Playback speed" aria-valuetext={speedLabel(speed)} list="speed-presets" />
      <datalist id="speed-presets">{SPEED_PRESETS.map((v) => <option key={v} value={toStep(v)} />)}</datalist>
      <button className="on num" onClick={() => onChange(1)} title="Reset to 1×">{speedLabel(speed)}</button>
    </div>
  );
}

function DemoCard({ demo, state, hour }) {
  const now = state?.now;
  const alerts = state?.alerts ?? [];
  const level = now?.level ?? "none";
  const arrived = hour >= demo.arrival_h;
  const first = (lvl) => alerts.find((a) => a.kind !== "fire-arrived" && LEVELS.indexOf(a.level) >= LEVELS.indexOf(lvl));
  const lead = (a) => (a ? demo.arrival_h - a.hour : null);
  const emailText = (e) => (e === "sent" ? "Email sent" : e === "sending" || e === "queued" ? "Sending email…" : `Email ${e}`);
  const callText = (c) => (c === "placed" ? `Called ${demo.phone}` : c === "calling" || c === "queued" ? `Calling ${demo.phone}…` : `Call ${c}`);
  const chip = (s, ok) => (s === ok ? "ok" : /^(not|failed)/.test(s) ? "bad" : "");
  const call = state?.call;
  return (
    <div className="card demo-card">
      <div className="card-head"><h3>Alert demo</h3><span className="num muted">T+{hour}h</span></div>
      <p className="small-p muted">
        The {demo.fire} ({new Date(demo.start).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}),
        replayed toward this address. Real wind, spread and satellite detections; only the location moves.
      </p>
      <div className="demo-address">
        <span className="muted">Watching</span><b>{demo.label}</b>
        <span className="muted">Emails to</span><b>{demo.email || "nobody (set SENTINEL_ALERT_EMAIL)"}</b>
        {demo.phone && <><span className="muted">Calls</span><b>{demo.calls ? `${demo.phone} at Evacuate` : `${demo.phone} (voice keys not set)`}</b></>}
      </div>
      <div className="demo-level" style={{ "--c": LEVEL_COLOR[level] }}>
        <b>{LEVEL_LABEL[level]}</b>
        <span className="num">{now?.nearest_fire_km != null ? `Fire ${now.nearest_fire_km.toFixed(1)} km away` : "No fire yet"}
          {now?.eta_h != null && now.eta_h < 24 ? ` · could arrive in ${Math.max(1, Math.round(now.eta_h))} h` : ""}</span>
      </div>
      <ol className="hh-alerts">
        {alerts.length === 0 && <li className="hh-empty">Press play. Alerts appear here as the sentinel sends them.</li>}
        {alerts.map((a) => (
          <li key={a.id} style={{ "--c": a.kind === "fire-arrived" ? "var(--ember)" : LEVEL_COLOR[a.level] }}>
            <span className="hh-time num">T+{(+a.hour).toFixed(a.kind === "fire-arrived" ? 1 : 0)}h</span>
            <div>
              <div className="hh-level">{a.kind === "fire-arrived" ? "Fire at your address" : LEVEL_LABEL[a.level]}</div>
              <p>{a.message}</p>
              <span className={`email-chip ${chip(a.email, "sent")}`}>{emailText(a.email)}</span>
              {a.call && <span className={`email-chip ${chip(a.call, "placed")}`}>{callText(a.call)}</span>}
            </div>
          </li>
        ))}
      </ol>
      {call && <CallLog call={call} />}
      <div className="arrivals demo-sum">
        <div><b className="num">{arrived ? "Arrived" : `T+${demo.arrival_h.toFixed(1)}h`}</b><span>Fire reaches the address</span></div>
        <div><b className="num ok">{first("warn") ? `${lead(first("warn")).toFixed(1)} h` : "–"}</b><span>warning before the fire</span></div>
        <div><b className="num ok">{first("evacuate") ? `${lead(first("evacuate")).toFixed(1)} h` : "–"}</b><span>told to leave before the fire</span></div>
      </div>
      <p className="hint">Drag the timeline back to reset and replay. Change the address in agent/.env.</p>
    </div>
  );
}

function LayerGroup({ title, items, layers, toggle }) {
  return (
    <div className="layer-group">
      <div className="group-title">{title}</div>
      {items.map(([k, label, color]) => (
        <label key={k} className="layer">
          <input type="checkbox" checked={!!layers[k]} onChange={() => toggle(k)} />
          <span className="swatch" style={{ "--c": color }} />
          <span className="layer-label">{label}</span>
          <span className="switch" aria-hidden="true" />
        </label>
      ))}
    </div>
  );
}

function Scrubber({ hour, max, start, onChange }) {
  const days = [];
  if (start) {
    const t0 = new Date(start);
    for (let h = 1; h <= max; h++) {
      const d = new Date(t0.getTime() + h * 3600e3);
      if (d.getHours() === 0) days.push([h, `${d.toLocaleDateString("en-US", { weekday: "short" })} ${d.getDate()}`]);
    }
  }
  return (
    <div className="scrubber" style={{ "--p": `${(hour / max) * 100}%` }}>
      <input type="range" min={0} max={max} value={hour} aria-label="Hours since ignition"
             onChange={(e) => onChange(+e.target.value)} />
      <div className="scrub-track" aria-hidden="true"><div className="scrub-fill" /></div>
      {days.map(([h, label]) => (
        <span key={h} className="scrub-day" style={{ left: `${(h / max) * 100}%` }}>{label}</span>
      ))}
    </div>
  );
}

function Metric({ v, u, l, hot }) {
  return (
    <div className={`metric ${hot ? "hot" : ""}`}>
      <b className="num">{v}<small>{u}</small></b>
      <span>{l}</span>
    </div>
  );
}

function ReplayCard({ homes, hour }) {
  if (!homes.length) {
    return (
      <div className="card">
        <h3>Sentinel replay</h3>
        <p className="muted small-p">Run <code>python -m evals.export_web</code> in <code>agent/</code> to load replayed households.</p>
      </div>
    );
  }
  const counts = Object.fromEntries(LEVELS.map((l) => [l, 0]));
  let arrived = 0, warned = 0, evac = 0, destroyed = 0;
  for (const h of homes) {
    counts[levelAt(h.alerts, hour)]++;
    if (!needed(h) || h.arrival_h == null) continue;
    destroyed++;
    if (h.arrival_h <= hour) {
      arrived++;
      if (h.first.warn != null && h.first.warn < h.arrival_h) warned++;
      if (h.first.evacuate != null && h.first.evacuate < h.arrival_h) evac++;
    }
  }
  return (
    <div className="card replay-card">
      <div className="card-head"><h3>Sentinel replay</h3><span className="num muted">T+{hour}h</span></div>
      <p className="small-p muted">{homes.length} households: {destroyed} {homes.some((h) => h.category === "burned") ? "inside the burn scar" : "later destroyed"}, the rest {homes.some((h) => h.category === "undamaged") ? "unharmed or " : ""}outside the burn. Each sees only what was known at this hour.</p>
      <div className="levelbar" role="img" aria-label={LEVELS.map((l) => `${counts[l]} ${LEVEL_LABEL[l]}`).join(", ")}>
        {LEVELS.map((l) => (
          <span key={l} style={{ flexGrow: counts[l], background: LEVEL_COLOR[l], opacity: l === "none" ? 0.35 : 1 }} />
        ))}
      </div>
      <div className="levelkey">
        {LEVELS.map((l) => (
          <span key={l}><i style={{ background: LEVEL_COLOR[l], opacity: l === "none" ? 0.5 : 1 }} />{LEVEL_LABEL[l]}<b className="num">{counts[l]}</b></span>
        ))}
      </div>
      <div className="arrivals">
        <div><b className="num">{arrived}<small>/{destroyed}</small></b><span>{homes.some((h) => h.category === "burned") ? "Burned places" : "Destroyed homes"} the fire has reached</span></div>
        <div><b className="num ok">{arrived ? pct(warned / arrived, 0) : "–"}</b><span>of those were told to get ready first</span></div>
        <div><b className="num ok">{arrived ? pct(evac / arrived, 0) : "–"}</b><span>were told to leave first</span></div>
      </div>
      <p className="hint">Select a household on the map to read its messages.</p>
    </div>
  );
}

function HouseholdCard({ home, hour, onClose, onJump }) {
  const label = { destroyed: "Destroyed home", undamaged: "Undamaged home", burned: "Inside the burn scar",
                  outside: "Outside the burn area" }[home.category];
  return (
    <div className="card household-card">
      <div className="card-head">
        <button className="back" onClick={onClose}>
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M7.5 2.5 4 6l3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.5" /></svg>
          All households
        </button>
      </div>
      <h3 className="hh-title">{label}</h3>
      <p className="small-p muted num">{home.lat.toFixed(4)}, {home.lon.toFixed(4)}</p>
      <dl className="hh-facts">
        <div><dt>Fire observed within 750 m</dt><dd className="num">{home.arrival_h != null ? `T+${home.arrival_h.toFixed(1)}h` : "Never"}</dd></div>
        {home.category === "outside" && <div><dt>Distance to final perimeter</dt><dd className="num">{home.dist_km.toFixed(1)} km</dd></div>}
        <div><dt>Alerts sent</dt><dd className="num">{home.alerts.length}</dd></div>
      </dl>
      <ol className="hh-alerts">
        {home.alerts.length === 0 && <li className="hh-empty">No alerts. The agent never judged this home at risk.</li>}
        {home.alerts.map((a, i) => {
          const past = a.hour <= hour;
          const lead = home.arrival_h != null ? home.arrival_h - a.hour : null;
          const arrivesBefore = home.arrival_h != null && a.hour >= home.arrival_h && (i === 0 || home.alerts[i - 1].hour < home.arrival_h);
          return (
            <Fragment key={a.hour}>
            {arrivesBefore && <ArrivalRow h={home.arrival_h} hour={hour} />}
            <li className={past ? "" : "future"} style={{ "--c": LEVEL_COLOR[a.level] }}>
              <button className="hh-time num" onClick={() => onJump(a.hour)} title="Jump to this hour">T+{a.hour}h</button>
              <div>
                <div className="hh-level">{LEVEL_LABEL[a.level]}
                  {lead != null && lead > 0 && <span className="num muted"> {lead.toFixed(1)} h before the fire</span>}
                </div>
                <p>{a.message}</p>
              </div>
            </li>
            </Fragment>
          );
        })}
        {home.arrival_h != null && home.alerts.every((a) => a.hour < home.arrival_h) && <ArrivalRow h={home.arrival_h} hour={hour} />}
      </ol>
    </div>
  );
}

function ArrivalRow({ h, hour }) {
  return (
    <li className={`hh-arrival ${h <= hour ? "" : "future"}`}>
      <span className="num">T+{h.toFixed(1)}h</span>
      <span>Satellites see fire within 750 m</span>
    </li>
  );
}

function IncidentCard({ data, now, reached }) {
  const m = data.info.metrics;
  return (
    <div className="card">
      <div className="card-head"><h3>Model against reality</h3></div>
      <dl className="facts">
        <div><dt>Ignition</dt><dd className="num">{new Date(data.info.start).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</dd></div>
        <div><dt>Modeled area now</dt><dd className="num">{Math.round(now.acres).toLocaleString()} ac</dd></div>
        {now.observed_acres != null && <div><dt>Seen by satellite now</dt><dd className="num">{Math.round(now.observed_acres).toLocaleString()} ac</dd></div>}
        <div><dt>Real final area</dt><dd className="num">{Math.round(data.info.real_acres).toLocaleString()} ac</dd></div>
        <div><dt>Structures reached by model</dt><dd className="num">{reached.n.toLocaleString()} <span className="muted">({reached.destroyed.toLocaleString()} destroyed)</span></dd></div>
        <div><dt>Shape overlap</dt><dd className="num">{pct(m.sorensen, 0)} <span className="muted">({pct(m.out_of_sample_sorensen, 0)} held out)</span></dd></div>
        {m.growth_skill != null && <div><dt>Growth match</dt><dd className="num">{pct(m.growth_skill, 0)} <span className="muted">({pct(m.out_of_sample_growth_skill, 0)} held out)</span></dd></div>}
      </dl>
    </div>
  );
}

function LiveBanner({ live }) {
  const rf = live?.redFlags?.features?.[0]?.properties;
  if (!rf) return null;
  return (
    <div className="banner" role="status">
      <span className="banner-dot" />
      <b>Red Flag Warning</b>
      <span>until {new Date(rf.ends).toLocaleString("en-US", { ...PT, weekday: "short", hour: "numeric", minute: "2-digit" })} PT, {live.redFlags.features.length} fire-weather zones</span>
    </div>
  );
}

function LiveSide({ live, onFocus, patterns, risk }) {
  const [scan, setScan] = useState(null);
  useEffect(() => {
    fetch("/data/sentinel/scan.json").then((r) => (r.ok ? r.json() : null)).then(setScan).catch(() => setScan(null));
  }, []);

  if (!live?.weather) {
    return (
      <div className="card">
        <h3>Live conditions</h3>
        <p className="small-p muted">Run <code>python -m wildfire live</code> in <code>modeling/</code> to load live data.</p>
      </div>
    );
  }
  const w = live.weather;
  const saNow = w.filter((p) => p.santa_ana_level_now > 0).length;
  const saSoon = w.filter((p) => p.santa_ana_level_peak_48h > 0).length;
  const extreme = w.filter((p) => p.santa_ana_level_peak_48h > 1).length;
  const driest = Math.min(...w.map((p) => p.min_fuel_moisture_48h_pct));
  const gust = Math.max(...w.map((p) => p.max_gust_48h_mph));
  const aqi = Math.max(0, ...(live.air || []).map((p) => p.us_aqi || 0));
  const big = (live.incidents || []).filter((i) => (i.acres || 0) >= 10).sort((a, b) => b.acres - a.acres);
  const src = live.status?.sources || {};

  // Group the scan by fire: one row per fire, worst level first.
  const byFire = [];
  for (const c of scan?.cells ?? []) {
    let g = byFire.find((x) => x.fire === c.fire);
    if (!g) byFire.push(g = { fire: c.fire, cells: [], level: c.level, nearest: c.nearest_fire_km });
    g.cells.push(c);
    if (c.level === "evacuate") g.level = "evacuate";
    g.nearest = Math.min(g.nearest, c.nearest_fire_km);
  }

  return (
    <>
      <div className="card">
        <div className="card-head"><h3>Where Sentinel would alert now</h3></div>
        {scan == null ? (
          <p className="small-p muted">Run <code>python -m evals.export_web</code> in <code>agent/</code> to scan the live feeds.</p>
        ) : byFire.length === 0 ? (
          <p className="small-p muted">No area in LA or Ventura counties is at get-ready or leave-now level.</p>
        ) : (
          <>
            <p className="small-p muted">A 5 km grid scored against every active fire. These areas would get alerts if someone there subscribed.</p>
            <ul className="scan-list">
              {byFire.slice(0, 6).map((g) => (
                <li key={g.fire}>
                  <button onClick={() => onFocus({ lon: g.cells[0].lon, lat: g.cells[0].lat, t: Date.now() })} style={{ "--c": LEVEL_COLOR[g.level] }}>
                    <span className="scan-level">{LEVEL_LABEL[g.level]}</span>
                    <span className="scan-fire">{titleCase(g.fire)}</span>
                    <span className="num muted">{g.cells.length} area{g.cells.length > 1 ? "s" : ""}, {g.nearest.toFixed(1)} km</span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      <div className="card">
        <div className="card-head"><h3>Fire weather</h3><span className="muted">next 48 h</span></div>
        <div className="metrics">
          <Metric v={saNow} u="" l="Points in Santa Ana now" hot={saNow > 0} />
          <Metric v={saSoon} u={`/${w.length}`} l="Within 48 h" hot={saSoon > 0} />
          <Metric v={extreme} u="" l="Extreme within 48 h" hot={extreme > 0} />
          <Metric v={driest} u="%" l="Driest 1-hour fuel" hot={driest < 5} />
          <Metric v={Math.round(gust)} u="mph" l="Peak gust" hot={gust >= 50} />
          <Metric v={aqi} u="" l="Highest US AQI" hot={aqi > 100} />
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h3>Active fires</h3><span className="muted">10 acres or more</span></div>
        {big.length ? (
          <ul className="rows">
            {big.slice(0, 6).map((i) => (
              <li key={i.name + i.lat}>
                <button onClick={() => onFocus({ lon: i.lon, lat: i.lat, t: Date.now() })}>
                  <span>{titleCase(i.name)}</span>
                  <span className="num muted">{Math.round(i.acres).toLocaleString()} ac{i.contained_pct != null ? `, ${i.contained_pct}% contained` : ""}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : <p className="small-p muted">None reported.</p>}
      </div>

      {patterns?.fire_weather && risk && (
        <div className="card">
          <div className="card-head"><h3>What drives big fires here</h3></div>
          {Object.entries(patterns.fire_weather.tiers).map(([name, t]) => (
            <p key={name} className="stat"><b className="num">{t.lift.toFixed(0)}×</b><span>{name} days ({t.days_per_year} a year) drive {pct(t.share_of_large_fire_acres, 0)} of large-fire acres.</span></p>
          ))}
          <p className="stat"><b className="num">{risk.auc.toFixed(2)}</b><span>AUC of the burn-likelihood model on 2015–25 fires it never saw.</span></p>
        </div>
      )}

      <div className="card">
        <div className="card-head"><h3>Feeds</h3></div>
        <ul className="feeds">
          {Object.entries(src).map(([k, v]) => (
            <li key={k}>
              <span><i className={`status ${v.ok ? "ok" : "bad"}`} />{k.replace("_", " ")}</span>
              <span className="muted">{v.ok ? v.summary : v.error?.includes("FIRMS_MAP_KEY") ? "Needs a FIRMS key" : "Unavailable"}</span>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}

function Legend({ live }) {
  const items = live
    ? [["area", "var(--ember)", "Sentinel alert area"], ["fill", "#e0245e", "Red Flag zone"], ["dot", "#ff3b1f", "Active fire"], ["dot", "#ffb000", "Hot spot"]]
    : [["dot", "var(--ember)", "Leave now"], ["dot", "var(--flare)", "Get ready"], ["ring", "#fff", "Later destroyed"], ["fill", "#e5482a", "Modeled spread"], ["fill", "#2b2521", "Destroyed building"], ["dash", "#fff", "Final perimeter"]];
  return (
    <div className="legend">
      {items.map(([kind, color, label]) => (
        <span key={label}><i className={`lg-${kind}`} style={{ "--c": color }} />{label}</span>
      ))}
    </div>
  );
}

function Segmented({ label, value, onChange, options }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map(([k, text]) => (
        <button key={k} className={k === value ? "on" : ""} aria-pressed={k === value} onClick={() => onChange(k)}>{text}</button>
      ))}
    </div>
  );
}

function titleCase(s) {
  if (!s) return "Unnamed fire";
  return s === s.toUpperCase() && !/\d/.test(s) ? s[0] + s.slice(1).toLowerCase() : s;
}
