import { useEffect, useMemo, useState } from "react";
import MapView, { BASEMAPS, CAMERAS, LIGHTS } from "./MapView.jsx";
import { Chart } from "./Charts.jsx";

const INCIDENTS = {
  live: "Live · Today",
  eaton_2025: "Eaton Fire · 2025",
  palisades_2025: "Palisades Fire · 2025",
};

const LIVE_LAYERS = [
  ["redFlags", "Red Flag Warnings (NWS)", "#e0245e"],
  ["santaAna", "Santa Ana Forecast · 48h", "#d9482b"],
  ["activeFires", "Active Fires (NIFC)", "#ff3b1f"],
  ["hotspots", "Satellite Hot Spots (FIRMS)", "#ffb000"],
  ["powerLines", "Power Lines (OSM)", "#ffd23f"],
];

const INCIDENT_LAYERS = [
  ["spread", "Fire Spread (Modeled)", "#e5482a"],
  ["real", "Real Perimeter (CAL FIRE)", "#ffffff"],
  ["structures", "Structures Reached (DINS)", "#b3261e"],
  ["detections", "Satellite Detections (FIRMS)", "#ffb000"],
];

const LAYER_LABELS = [
  ["risk", "10-yr Burn Likelihood", "#f08a2c"],
  ["timesBurned", "Times Burned since 1950", "#e8873a"],
  ["history", "Fire History 1950–2025", "#f3d38a"],
];

const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const compass = (deg) => COMPASS[Math.round(deg / 22.5) % 16];
const pct = (v) => `${Math.round(v * 100)}%`;

async function loadIncident(key) {
  const get = (f) => fetch(`/data/incidents/${key}/${f}`).then((r) => r.json());
  const [info, series, perimeters, real, structures, detections] = await Promise.all([
    get("incident.json"), get("timeseries.json"), get("perimeters.geojson"),
    get("real_perimeter.geojson"), get("structures.geojson"),
    get("detections.geojson").catch(() => ({ type: "FeatureCollection", features: [] })),
  ]);
  return { info, series, perimeters, real, structures, detections };
}

export default function App() {
  const [incident, setIncident] = useState("live");
  const [live, setLive] = useState(null);
  const [data, setData] = useState(null);
  const [hour, setHour] = useState(12);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [mapReady, setMapReady] = useState(false);
  const [patterns, setPatterns] = useState(null);
  const [risk, setRisk] = useState(null);
  const [layers, setLayers] = useState({
    spread: true, real: true, structures: true, detections: true, risk: false, timesBurned: false, history: false,
    redFlags: true, santaAna: true, activeFires: true, hotspots: true, powerLines: false,
  });
  const isLive = incident === "live";
  const [basemap, setBasemap] = useState("standard");
  const [light, setLight] = useState("dusk");
  const [camera, setCamera] = useState("3d");

  useEffect(() => {
    fetch("/data/patterns.json").then((r) => r.json()).then(setPatterns);
    fetch("/data/risk_model_metrics.json").then((r) => r.json()).then(setRisk);
    Promise.all(["status.json", "weather.json", "red_flags.geojson", "air_quality.json", "incidents.json"]
      .map((f) => fetch(`/data/live/${f}`).then((r) => (r.ok ? r.json() : null)).catch(() => null)))
      .then(([status, weather, redFlags, air, incidents]) => setLive({ status, weather, redFlags, air, incidents }));
  }, []);

  useEffect(() => {
    setData(null);
    setPlaying(false);
    if (incident === "live") return;
    loadIncident(incident).then((d) => { setData(d); setHour(12); });
  }, [incident]);

  useEffect(() => {
    if (!playing || !data) return;
    const id = setInterval(() => {
      setHour((h) => {
        if (h >= data.info.hours) { setPlaying(false); return h; }
        return h + 1;
      });
    }, 400 / speed);
    return () => clearInterval(id);
  }, [playing, speed, data]);

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

  const clock = now && new Date(now.time).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const m = data?.info.metrics;
  const fw = patterns?.fire_weather;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand"><span className="logo">▲</span> FireTrace</div>
        <span className="sub">Wildfire Forensic Analysis</span>
        <span className="divider" />
        <select value={incident} onChange={(e) => setIncident(e.target.value)}>
          {Object.entries(INCIDENTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <span className="sub">Los Angeles County, CA</span>
        <span className="spacer" />
        {!isLive && clock && <span className="pill">{clock} · T+{hour}h</span>}
        {isLive && live?.status && <span className="pill live-pill">● Updated {new Date(live.status.updated).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "America/Los_Angeles" })} PT</span>}
      </header>

      <main className="body">
        <section className="mapwrap">
          <MapView incident={incident} data={data} hour={hour} layers={layers}
                   basemap={basemap} light={light} camera={camera} onReady={() => setMapReady(true)} />
          <div className="viewbar">
            <Segmented value={basemap} onChange={setBasemap}
                       options={Object.entries(BASEMAPS).map(([k, v]) => [k, v.label])} />
            {BASEMAPS[basemap].standard && (
              <Segmented value={light} onChange={setLight}
                         options={LIGHTS.map((l) => [l, l[0].toUpperCase() + l.slice(1)])} />
            )}
            <Segmented value={camera} onChange={setCamera}
                       options={Object.entries(CAMERAS).map(([k, v]) => [k, v.label])} />
          </div>
          {!isLive && !data && <div className="loading">Loading incident…</div>}
          {isLive && <LiveBanner live={live} />}
          <div className="legend" style={{ display: isLive ? "none" : undefined }}>
            <span><i style={{ background: "#e5482a" }} /> Simulated burn area</span>
            <span><i className="dash" /> Real final perimeter</span>
            <span><i className="dot" style={{ background: "#b3261e" }} /> Destroyed</span>
            <span><i className="dot" style={{ background: "#7fbf7a" }} /> No damage</span>
            <span><i className="dot" style={{ background: "#ffb000" }} /> Satellite detection</span>
          </div>
        </section>

        <aside className="side">
          <div className="card">
            <h3>Layers</h3>
            <div className="group">{isLive ? "Live" : "Incident"}</div>
            {(isLive ? LIVE_LAYERS : INCIDENT_LAYERS).map(([k, label, color]) => (
              <label key={k} className="layer">
                <input type="checkbox" checked={layers[k]}
                       onChange={() => setLayers((l) => ({ ...l, [k]: !l[k] }))} />
                <i style={{ background: color }} />
                {label}
              </label>
            ))}
            <div className="group">Regional history</div>
            {LAYER_LABELS.map(([k, label, color]) => (
              <label key={k} className="layer">
                <input type="checkbox" checked={layers[k]}
                       onChange={() => setLayers((l) => ({ ...l, [k]: !l[k] }))} />
                <i style={{ background: color }} />
                {label}
              </label>
            ))}
          </div>

          {isLive && <LiveCard live={live} />}

          {data && (
            <div className="card">
              <h3>Incident Overview</h3>
              <dl>
                <dt>Incident</dt><dd>{data.info.name}</dd>
                <dt>Ignition</dt><dd>{new Date(data.info.start).toLocaleString()}</dd>
                <dt>Simulated area</dt><dd>{Math.round(now.acres).toLocaleString()} ac</dd>
                <dt>Real final area</dt><dd>{Math.round(data.info.real_acres).toLocaleString()} ac</dd>
                <dt>Structures reached</dt><dd>{reached.n.toLocaleString()} <span className="muted">({reached.destroyed.toLocaleString()} destroyed)</span></dd>
                {now.observed_acres != null && <><dt>Satellite-observed</dt><dd>{Math.round(now.observed_acres).toLocaleString()} ac</dd></>}
                <dt>Shape fit</dt><dd>{pct(m.sorensen)} overlap <span className="muted">({pct(m.out_of_sample_sorensen)} out-of-sample)</span></dd>
                {m.growth_skill != null && <><dt>Growth match</dt><dd>{pct(m.growth_skill)} vs satellite <span className="muted">({pct(m.out_of_sample_growth_skill)} out-of-sample)</span></dd></>}
              </dl>
            </div>
          )}

          {now && (
            <div className="card">
              <h3>Weather & Fire Behavior <span className="muted">at T+{hour}h</span></h3>
              <div className="wx">
                <div><b>{now.wind_mph}</b><small>mph wind {compass(now.wind_dir_deg)}</small></div>
                <div><b>{now.gust_mph}</b><small>mph gusts</small></div>
                <div><b className={now.rh_pct < 15 ? "hot" : ""}>{now.rh_pct}%</b><small>humidity</small></div>
                <div><b>{Math.round(now.temp_f)}°F</b><small>temp</small></div>
                <div><b className={now.fuel_moisture_1h_pct < 5 ? "hot" : ""}>{now.fuel_moisture_1h_pct}%</b><small>1-h fuel moisture</small></div>
                <div><b>{Math.round(now.growth_acres_per_hr)}</b><small>ac/hr growth</small></div>
              </div>
            </div>
          )}

          {fw && risk && (
            <div className="card">
              <h3>Regional Patterns <span className="muted">LA + Ventura, 1950–2025</span></h3>
              {Object.entries(fw.tiers).map(([name, t]) => (
                <p key={name} className="stat"><b>{t.lift.toFixed(0)}×</b> {name} days ({t.days_per_year}/yr) drive {pct(t.share_of_large_fire_acres)} of large-fire acres.</p>
              ))}
              <p className="stat"><b>{patterns.n_fires_since_1950.toLocaleString()}</b> mapped fires since 1950.</p>
              <p className="stat"><b>{risk.auc.toFixed(2)}</b> AUC of the burn-likelihood model on 2015–25 fires it never saw.</p>
            </div>
          )}
        </aside>
      </main>

      <footer className="timeline" style={{ display: isLive ? "none" : undefined }}>
        <div className="controls">
          <button onClick={() => { if (data && hour >= data.info.hours) setHour(0); setPlaying((p) => !p); }} disabled={!data}>
            {playing ? "❚❚" : "▶"}
          </button>
          <select value={speed} onChange={(e) => setSpeed(+e.target.value)}>
            {[1, 2, 4].map((s) => <option key={s} value={s}>{s}×</option>)}
          </select>
          <input type="range" min={0} max={data?.info.hours ?? 72} value={hour}
                 onChange={(e) => { setPlaying(false); setHour(+e.target.value); }} />
          <span className="pill">T+{hour}h</span>
        </div>
        {data && (
          <div className="charts">
            <Chart title="Fire Area" unit="acres (model / satellite)" rows={data.series} hour={hour}
                   series={[{ key: "acres", color: "#e5482a", fill: true, suffix: " /" },
                            ...(data.series[0].observed_acres != null ? [{ key: "observed_acres", color: "#c98a00" }] : [])]} />
            <Chart title="Growth Rate" unit="acres/hr" rows={data.series} hour={hour}
                   series={[{ key: "growth_acres_per_hr", color: "#f08a2c", fill: true }]} />
            <Chart title="Wind" unit="mph (sustained / gust)" rows={data.series} hour={hour}
                   series={[{ key: "wind_mph", color: "#3b6ea8", suffix: " /" }, { key: "gust_mph", color: "#8fb3dc" }]} />
            <Chart title="1-h Fuel Moisture" unit="%" rows={data.series} hour={hour}
                   series={[{ key: "fuel_moisture_1h_pct", color: "#6b8e3a", fill: true }]} />
          </div>
        )}
      </footer>
    </div>
  );
}

function LiveBanner({ live }) {
  const rf = live?.redFlags?.features?.[0]?.properties;
  if (!rf) return null;
  return (
    <div className="banner">
      <b>RED FLAG WARNING</b> until {new Date(rf.ends).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit", timeZone: "America/Los_Angeles" })} PT · {live.redFlags.features.length} fire-weather zones
    </div>
  );
}

function LiveCard({ live }) {
  if (!live?.weather) return <div className="card"><h3>Live Conditions</h3><p className="muted">Run <code>python -m wildfire live</code> to load live data.</p></div>;
  const w = live.weather;
  const now = w.filter((p) => p.santa_ana_level_now > 0).length;
  const soon = w.filter((p) => p.santa_ana_level_peak_48h > 0).length;
  const extreme = w.filter((p) => p.santa_ana_level_peak_48h > 1).length;
  const driest = Math.min(...w.map((p) => p.min_fuel_moisture_48h_pct));
  const gust = Math.max(...w.map((p) => p.max_gust_48h_mph));
  const aqi = Math.max(0, ...(live.air || []).map((p) => p.us_aqi || 0));
  const big = (live.incidents || []).filter((i) => (i.acres || 0) >= 10).sort((a, b) => b.acres - a.acres);
  const src = live.status?.sources || {};
  return (
    <>
      <div className="card">
        <h3>Live Conditions <span className="muted">next 48h</span></h3>
        <div className="wx">
          <div><b className={now ? "hot" : ""}>{now}</b><small>Santa Ana now</small></div>
          <div><b className={soon ? "hot" : ""}>{soon}<span className="muted">/{w.length}</span></b><small>points ≤48h</small></div>
          <div><b className={extreme ? "hot" : ""}>{extreme}</b><small>extreme ≤48h</small></div>
          <div><b className={driest < 5 ? "hot" : ""}>{driest}%</b><small>driest 1-h fuel</small></div>
          <div><b>{Math.round(gust)}</b><small>mph peak gust</small></div>
          <div><b>{aqi}</b><small>max US AQI</small></div>
        </div>
      </div>
      <div className="card">
        <h3>Active Fires <span className="muted">≥10 acres</span></h3>
        {big.length ? big.slice(0, 6).map((i) => (
          <div key={i.name + i.lat} className="row">
            <span>{i.name}</span>
            <span className="muted">{Math.round(i.acres).toLocaleString()} ac{i.contained_pct != null ? ` · ${i.contained_pct}%` : ""}</span>
          </div>
        )) : <p className="muted">None reported.</p>}
      </div>
      <div className="card">
        <h3>Feeds</h3>
        {Object.entries(src).map(([k, v]) => (
          <div key={k} className="row">
            <span><i className={`status ${v.ok ? "ok" : "bad"}`} />{k.replace("_", " ")}</span>
            <span className="muted small">{v.ok ? v.summary : v.error.includes("FIRMS_MAP_KEY") ? "needs key" : "error"}</span>
          </div>
        ))}
      </div>
    </>
  );
}

function Segmented({ value, onChange, options }) {
  return (
    <div className="seg">
      {options.map(([k, label]) => (
        <button key={k} className={k === value ? "on" : ""} onClick={() => onChange(k)}>{label}</button>
      ))}
    </div>
  );
}
