import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import "mapbox-gl/dist/mapbox-gl.css";
import "../styles.css";
import "./surge.css";
import SurgeMapView, { CAMERAS, CAT_HEX, stormState } from "./SurgeMapView.jsx";
import { BASEMAPS, LIGHTS } from "../MapView.jsx";
import { loadTerrain } from "../water.js";
import { Chart } from "../Charts.jsx";
import { navigate, pct } from "../shared.jsx";
import { RouteCard, useSafeRoute } from "../RoutePanel.jsx";
import { SURGE_LEVEL_COLOR as LEVEL_COLOR, SURGE_LEVEL_LABEL as LEVEL_LABEL, SurgeLogo, catLabel } from "./common.jsx";

const STORMS = [
  ["live", "Live"],
  ["sandy_2012", "Sandy 2012"],
  ["matthew_2016", "Matthew 2016"],
  ["irma_2017", "Irma 2017"],
  ["isabel_2003", "Isabel 2003"],
];
const LIVE_LAYERS = [
  ["liveAlerts", "NWS coastal warnings", "#ff5f6d"],
  ["liveGauges", "Tide gauges, water above tide", "#2fb7c9"],
  ["liveStorms", "Active Atlantic storms", "#ffd166"],
];
const STORM_LAYERS = [
  ["households", "Sentinel households", "var(--signal)"],
  ["water", "Flood water (gauge-observed)", "#1aa6c4"],
  ["sky", "Hurricane clouds & rain (3D)", "#c9d6e2"],
  ["extent", "Peak flood outline", "#e9f7ff"],
  ["track", "Best track", "#ffa24c"],
  ["cone", "NHC forecast cone", "#e9f3f5"],
  ["radii", "Wind radii (39/58/74 mph)", "#ffd166"],
  ["gauges", "Tide gauges", "#9fd8cb"],
  ["wind", "Wind", "#9fb7d6"],
];
const LEVELS = ["none", "watch", "warn", "evacuate"];
const ET = { timeZone: "America/New_York" };
const EMPTY_FC = { type: "FeatureCollection", features: [] };

async function loadStorm(key) {
  const base = `/data/surge/storms/${key}`;
  const get = (f) => fetch(`${base}/${f}`).then((r) => r.json());
  const [info, gauges, advisories, series, extent, terrain] = await Promise.all([
    get("storm.json"), get("gauges.json"), get("advisories.geojson"), get("timeseries.json"),
    get("extent.geojson").catch(() => EMPTY_FC), loadTerrain(base),
  ]);
  return { key, info, gauges, advisories, series, extent, terrain };
}

const api = (path, body) => fetch(`/api/surge/${path}`, body === undefined ? undefined : {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
}).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });

const levelAt = (alerts, hour) => {
  let lvl = "none";
  for (const a of alerts) if (a.hour <= hour) lvl = a.level;
  return lvl;
};
const fmtTime = (iso, opts = {}) => new Date(iso).toLocaleString("en-US", { ...ET, month: "short", day: "numeric", hour: "numeric", minute: "2-digit", ...opts });

export default function SurgeApp() {
  const [key, setKey] = useState(() => new URLSearchParams(window.location.search).get("storm") || "sandy_2012");
  const [storm, setStorm] = useState(null);
  const [live, setLive] = useState(null);
  const [replay, setReplay] = useState(null);
  const [hour, setHour] = useState(48);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [selected, setSelected] = useState(null);
  const [focus, setFocus] = useState(null);
  const [layersOpen, setLayersOpen] = useState(() => window.innerWidth > 900);
  const [layers, setLayers] = useState({
    households: true, water: true, sky: true, extent: false, track: true, cone: true, radii: true, gauges: true, wind: true,
    liveAlerts: true, liveGauges: true, liveStorms: true,
  });
  const [basemap, setBasemap] = useState("standard");
  const [light, setLight] = useState("dusk");
  const [camera, setCamera] = useState("3d");
  const [demo, setDemo] = useState(null);           // server config once an address is watched
  const [picking, setPicking] = useState(false);
  const [demoState, setDemoState] = useState(null);
  const [demoError, setDemoError] = useState(null);
  const isLive = key === "live";
  const route = useSafeRoute({ mode: isLive ? "live" : `replay:${key}`, departHour: hour, hazard: "surge" });

  useEffect(() => {
    document.title = "SurgeTrace map";
    const json = (p) => fetch(p).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    json("/data/surge/sentinel/replay.json").then(setReplay);
    Promise.all(["status.json", "gauges.json", "storms.json", "alerts.geojson"].map((f) => json(`/data/surge/live/${f}`)))
      .then(([status, gauges, storms, alerts]) => setLive({ status, gauges, storms, alerts }));
  }, []);

  useEffect(() => {
    setStorm(null);
    setPlaying(false);
    setSelected(null);
    setDemo(null);
    setPicking(false);
    const url = new URL(window.location.href);
    url.searchParams.set("storm", key);
    window.history.replaceState({}, "", url);
    if (key === "live") return;
    loadStorm(key).then((d) => { setStorm(d); setHour(Math.max(0, d.info.peak.hour - 30)); });
  }, [key]);

  // Alert demo: every hour the map shows, the sentinel decides for the watched address.
  // Fast playback sends steps faster than they return; only the response for the hour on screen counts.
  const hourRef = useRef(hour);
  hourRef.current = hour;
  useEffect(() => {
    if (!demo || !storm) return;
    api("step", { hour }).then((s) => { if (s.hour === Math.min(hourRef.current, storm.info.hours)) setDemoState(s); })
      .catch(() => setDemoError("Lost the demo server."));
  }, [hour, demo, storm]);
  useEffect(() => {
    if (!demo) return;
    const id = setInterval(() => api("state").then((s) => { if (s.hour === hourRef.current) setDemoState(s); }).catch(() => {}), 1000);
    return () => clearInterval(id);
  }, [demo]);

  const watch = async (pt, label) => {
    try {
      const cfg = await api("config", { storm: key, lat: pt.lat, lon: pt.lon, label: label || null });
      setDemoState(await api("reset", {}));
      setDemoError(null);
      setPicking(false);
      setSelected(null);
      setDemo(cfg);
      setHour(0);
      setPlaying(false);
      setFocus({ lon: pt.lon, lat: pt.lat, zoom: 13, t: Date.now() });
    } catch {
      setDemoError("Start the demo server first (in agent/: .venv/bin/python -m sentinel demo-server).");
      setPicking(false);
    }
  };
  const stopDemo = () => { setDemo(null); setDemoState(null); setPicking(false); };

  useEffect(() => {
    if (!playing || !storm) return;
    const id = setInterval(() => {
      setHour((h) => {
        if (h >= storm.info.hours) { setPlaying(false); return h; }
        return h + 1;
      });
    }, 380 / speed);
    return () => clearInterval(id);
  }, [playing, speed, storm]);

  useEffect(() => {
    if (!storm) return;
    const on = (e) => {
      if (e.target.closest("input, select, textarea, button")) return;
      if (e.code === "Space") { e.preventDefault(); togglePlay(); }
      if (e.key === "ArrowRight") { setPlaying(false); setHour((h) => Math.min(storm.info.hours, h + 1)); }
      if (e.key === "ArrowLeft") { setPlaying(false); setHour((h) => Math.max(0, h - 1)); }
      if (e.key === "Escape") setPicking(false);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  });

  const togglePlay = () => {
    if (storm && hour >= storm.info.hours) setHour(0);
    setPlaying((p) => !p);
  };

  const now = storm?.series[Math.min(hour, storm.series.length - 1)];
  const homes = useMemo(() => (replay?.households ?? []).map((h, idx) => ({ ...h, idx })).filter((h) => h.storm === key), [replay, key]);
  const homesGeo = useMemo(() => ({
    type: "FeatureCollection",
    features: homes.map((h) => ({
      type: "Feature", geometry: { type: "Point", coordinates: [h.lon, h.lat] },
      properties: { idx: h.idx, level: levelAt(h.alerts, hour), flooded: h.category === "flooded", selected: h.idx === selected },
    })),
  }), [homes, hour, selected]);
  const demoHome = demo && { lon: demo.lon, lat: demo.lat, level: demoState?.now?.level ?? "none", label: "Watched address" };

  const clock = now && fmtTime(now.time);
  const toggle = (k) => setLayers((l) => ({ ...l, [k]: !l[k] }));
  const selectedHome = selected != null ? replay?.households[selected] : null;
  const anchor = useMemo(() => {
    // Default watched address: the deepest-flooded household the rules policy evacuated at least 12 h ahead.
    let best = null;
    for (const h of homes) {
      if (h.category !== "flooded" || h.first.evacuate == null || h.arrival_h - h.first.evacuate < 12) continue;
      if (!best || h.peak_depth_ft > best.peak_depth_ft) best = h;
    }
    return best;
  }, [homes]);

  return (
    <div className={`app surge ${isLive ? "is-live" : "is-incident"}`}>
      <header className="topbar">
        <a href="/surge" onClick={navigate} className="topbar-logo" aria-label="SurgeTrace home"><SurgeLogo /></a>
        <nav className="hazard-switch" aria-label="Hazard">
          <a href="/map" onClick={navigate}>Fire</a>
          <a href="/surge/map" className="on" aria-current="page">Surge</a>
        </nav>
        <nav className="incident-tabs" aria-label="Choose a view">
          {STORMS.map(([k, label]) => (
            <button key={k} className={k === key ? "on" : ""} aria-pressed={k === key} onClick={() => setKey(k)}>
              {k === "live" && <span className="live-dot" aria-hidden="true" />}{label}
            </button>
          ))}
        </nav>
        {!isLive && (demo || picking
          ? <button className="demo-btn on" onClick={stopDemo}>Exit alert demo</button>
          : <button className="demo-btn" onClick={() => { setPicking(true); setDemoError(null); setPlaying(false); }}
                    title="Pick any address and get the alerts the sentinel would have sent, by email">
              <span className="demo-dot" aria-hidden="true" />Alert demo
            </button>)}
        <span className="topbar-spacer" />
        <span className="topbar-meta">
          {isLive && live?.status && <>Feeds updated <b className="num">{fmtTime(live.status.updated)} ET</b></>}
          {!isLive && storm && <>{storm.info.place}, replayed with only the NHC forecasts issued by each hour</>}
        </span>
      </header>

      <main className="body">
        <section className="mapwrap">
          <SurgeMapView live={isLive} storm={storm} hour={hour} playRate={playing ? speed / 0.38 : 0} layers={layers}
                        households={demo ? EMPTY_FC : homesGeo} home={demoHome} basemap={basemap} light={light} camera={camera}
                        focus={focus} onSelectHousehold={setSelected} picking={picking}
                        onPick={(pt) => watch(pt)} route={demo ? null : route} onMapPick={route.onMapPick} />

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
                <LayerGroup title={isLive ? "Now" : "This storm"} items={isLive ? LIVE_LAYERS : STORM_LAYERS} layers={layers} toggle={toggle} />
              </div>
            </div>
          </div>

          <div className="viewbar">
            <Segmented label="Basemap" value={basemap} onChange={setBasemap} options={Object.entries(BASEMAPS).map(([k, v]) => [k, v.label])} />
            {BASEMAPS[basemap].standard && (
              <Segmented label="Light" value={light} onChange={setLight} options={LIGHTS.map((l) => [l, l[0].toUpperCase() + l.slice(1)])} />
            )}
            {!isLive && <Segmented label="Camera" value={camera} onChange={setCamera} options={Object.entries(CAMERAS).map(([k, v]) => [k, v.label])} />}
          </div>

          {!isLive && !storm && <div className="loading"><span className="spinner" />Loading the {STORMS.find(([k]) => k === key)[1]} replay</div>}
          {isLive && <LiveBanner live={live} />}
          {picking && (
            <div className="banner pick-banner" role="status">
              <span className="banner-dot" />
              <b>Click any address on the map</b>
              <span>The sentinel will watch it through {storm?.info.name ?? "the storm"} and email each alert.</span>
              {anchor && <button className="pick-anchor" onClick={() => watch(anchor, "A home that flooded")}>Use a home that flooded</button>}
            </div>
          )}
          {demoError && <div className="banner demo-error" role="alert"><b>Alert demo</b><span>{demoError}</span>
            <button onClick={() => setDemoError(null)} aria-label="Dismiss">×</button></div>}
          <Legend live={isLive} />
        </section>

        <aside className="side">
          {/* Flood-safe routing: storm replays, and live only while an Atlantic storm is active (its warned region). */}
          {!demo && ((!isLive && storm) || (isLive && live?.storms?.some((s) => s.id.startsWith("al")))) &&
            <RouteCard route={route} hourLabel={`leave at T+${hour}h`} />}
          {isLive ? (
            <LiveSide live={live} onFocus={setFocus} />
          ) : demo ? (
            <DemoCard demo={demo} state={demoState} hour={hour} storm={storm} />
          ) : selectedHome ? (
            <HouseholdCard home={selectedHome} hour={hour} onClose={() => setSelected(null)} onJump={(h) => { setPlaying(false); setHour(h); }} />
          ) : (
            storm && now && (
              <>
                <ReplayCard homes={homes} hour={hour} />
                <StormCard storm={storm} now={now} hour={hour} clock={clock} />
                <ModelCard storm={storm} />
              </>
            )
          )}
        </aside>
      </main>

      {!isLive && (
        <footer className="timeline">
          <div className="controls">
            <button className="play" onClick={togglePlay} disabled={!storm} aria-label={playing ? "Pause" : "Play"}>
              {playing
                ? <svg width="12" height="12" viewBox="0 0 12 12"><rect x="2" y="1.5" width="3" height="9" rx="1" /><rect x="7" y="1.5" width="3" height="9" rx="1" /></svg>
                : <svg width="12" height="12" viewBox="0 0 12 12"><path d="M3 1.6v8.8a.6.6 0 0 0 .9.5l7-4.4a.6.6 0 0 0 0-1L3.9 1.1a.6.6 0 0 0-.9.5Z" /></svg>}
            </button>
            <div className="speed" role="group" aria-label="Playback speed">
              {[1, 2, 4].map((s) => <button key={s} className={s === speed ? "on" : ""} onClick={() => setSpeed(s)}>{s}×</button>)}
            </div>
            <Scrubber hour={hour} max={storm?.info.hours ?? 96} start={storm?.info.start} peak={storm?.info.peak.hour}
                      onChange={(h) => { setPlaying(false); setHour(h); }} />
            <div className="clock">
              <span className="num">{storm ? `${hour - storm.info.peak.hour >= 0 ? "+" : "−"}${Math.abs(hour - storm.info.peak.hour)}h from peak` : "–"}</span>
              <small className="num">{clock ? `${clock} ET` : "–"}</small>
            </div>
          </div>
          {storm && (
            <div className="charts">
              <Chart title={`Water at ${storm.info.peak.gauge.split(",")[0]}`} unit="ft" rows={storm.series} hour={hour} digits={1}
                     series={[{ key: "water_ft", color: "#4fd1e8", fill: true, label: "Observed" }, { key: "tide_ft", color: "#7d95a6", label: "Tide" }]} />
              <Chart title="Surge above tide" unit="ft" rows={storm.series} hour={hour} digits={1}
                     series={[{ key: "surge_ft", color: "#7ff0d0", fill: true, label: "Observed" }, { key: "model_surge_ft", color: "#ffc145", label: "Model" }]} />
              <Chart title="Land under water" unit="acres" rows={storm.series} hour={hour}
                     series={[{ key: "flooded_acres", color: "#2fb7c9", fill: true }]} />
              <Chart title="Wind" unit="mph" rows={storm.series} hour={hour}
                     series={[{ key: "wind_mph", color: "#8fb3dc", fill: true, label: "Sustained" }, { key: "gust_mph", color: "#c7d9ee", label: "Gust" }]} />
            </div>
          )}
        </footer>
      )}
    </div>
  );
}

function StormCard({ storm, now, hour, clock }) {
  const st = stormState(storm.info.best_track, hour);
  return (
    <div className="card">
      <div className="card-head"><h3>{storm.info.name}</h3><span className="num muted">{clock}</span></div>
      <div className="metrics">
        <Metric v={st ? Math.round(st.vmax_kt * 1.15) : "–"} u="mph" l={st ? catLabel(st.cat) : "Not formed"} c={st ? CAT_HEX[st.cat] : null} />
        <Metric v={now.storm_km != null ? Math.round(now.storm_km * 0.621) : "–"} u="mi" l="Centre from the city" />
        <Metric v={now.storm_pmin_mb ?? "–"} u="mb" l="Central pressure" />
        <Metric v={now.water_ft != null ? now.water_ft.toFixed(1) : "–"} u="ft" l="Water at the gauge" hot={now.surge_ft >= 3} />
        <Metric v={now.surge_ft != null ? `${now.surge_ft >= 0 ? "+" : ""}${now.surge_ft.toFixed(1)}` : "–"} u="ft" l="Above the tide" hot={now.surge_ft >= 3} />
        <Metric v={Math.round(now.flooded_acres).toLocaleString()} u="ac" l="Land under water" hot={now.flooded_acres > 500} />
      </div>
    </div>
  );
}

function ModelCard({ storm }) {
  const m = storm.info.model.gauges;
  return (
    <div className="card">
      <div className="card-head"><h3>Surge model against the gauges</h3>
        <span className={`role-chip ${storm.info.role}`}>{storm.info.role === "train" ? "Calibration storm" : "Held out"}</span></div>
      <p className="small-p muted">Peak surge above the tide, modelled from the best track with parameters fitted on Sandy and Matthew only.</p>
      <dl className="facts">
        {Object.entries(m).map(([id, g]) => (
          <div key={id}><dt>{storm.gauges[id]?.name}</dt>
            <dd className="num">{g.peak_sim_ft.toFixed(1)} <span className="muted">model vs</span> {g.peak_obs_ft.toFixed(1)} ft</dd></div>
        ))}
        <div><dt>Peak water, {storm.info.peak.gauge}</dt><dd className="num">{storm.info.peak.water_ft.toFixed(1)} ft NAVD88</dd></div>
        <div><dt>Land flooded at the peak</dt><dd className="num">{storm.info.peak.flooded_acres.toLocaleString()} ac</dd></div>
      </dl>
    </div>
  );
}

function ReplayCard({ homes, hour }) {
  if (!homes.length) {
    return (
      <div className="card">
        <h3>Sentinel replay</h3>
        <p className="muted small-p">Run <code>python -m evals.surge.export_web</code> in <code>agent/</code> to load replayed households.</p>
      </div>
    );
  }
  const counts = Object.fromEntries(LEVELS.map((l) => [l, 0]));
  let arrived = 0, warned = 0, evac = 0, flooded = 0;
  for (const h of homes) {
    counts[levelAt(h.alerts, hour)]++;
    if (h.category !== "flooded") continue;
    flooded++;
    if (h.arrival_h <= hour) {
      arrived++;
      if (h.first.warn != null && h.first.warn < h.arrival_h) warned++;
      if (h.first.evacuate != null && h.first.evacuate < h.arrival_h) evac++;
    }
  }
  return (
    <div className="card replay-card">
      <div className="card-head"><h3>Sentinel replay</h3><span className="num muted">T+{hour}h</span></div>
      <p className="small-p muted">{homes.length} households: {flooded} that flooded, the rest low-lying homes that stayed dry or high ground. Each sees only the forecasts issued by this hour.</p>
      <div className="levelbar" role="img" aria-label={LEVELS.map((l) => `${counts[l]} ${LEVEL_LABEL[l]}`).join(", ")}>
        {LEVELS.map((l) => <span key={l} style={{ flexGrow: counts[l], background: LEVEL_COLOR[l], opacity: l === "none" ? 0.35 : 1 }} />)}
      </div>
      <div className="levelkey">
        {LEVELS.map((l) => (
          <span key={l}><i style={{ background: LEVEL_COLOR[l], opacity: l === "none" ? 0.5 : 1 }} />{LEVEL_LABEL[l]}<b className="num">{counts[l]}</b></span>
        ))}
      </div>
      <div className="arrivals">
        <div><b className="num">{arrived}<small>/{flooded}</small></b><span>Flooded homes the water has reached</span></div>
        <div><b className="num ok">{arrived ? pct(warned / arrived, 0) : "–"}</b><span>of those were told to get ready first</span></div>
        <div><b className="num ok">{arrived ? pct(evac / arrived, 0) : "–"}</b><span>were told to leave first</span></div>
      </div>
      <p className="hint">Select a household on the map to read its messages.</p>
    </div>
  );
}

function HouseholdCard({ home, hour, onClose, onJump }) {
  const label = { flooded: "A home that flooded", spared: "Low-lying, stayed dry", high: "High ground" }[home.category];
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
        <div><dt>Ground</dt><dd className="num">{home.ground_ft != null ? `${home.ground_ft.toFixed(1)} ft` : "above 40 ft"}</dd></div>
        <div><dt>Water reaches it at</dt><dd className="num">{home.spill_ft != null ? `${home.spill_ft.toFixed(1)} ft` : "never"}</dd></div>
        <div><dt>Observed flooding</dt><dd className="num">{home.arrival_h != null ? `T+${home.arrival_h.toFixed(1)}h, ${home.peak_depth_ft.toFixed(1)} ft deep` : "None"}</dd></div>
        <div><dt>Alerts sent</dt><dd className="num">{home.alerts.length}</dd></div>
      </dl>
      <ol className="hh-alerts">
        {home.alerts.length === 0 && <li className="hh-empty">No alerts. The agent never judged this home at risk.</li>}
        {home.alerts.map((a, i) => {
          const lead = home.arrival_h != null ? home.arrival_h - a.hour : null;
          const arrivesBefore = home.arrival_h != null && a.hour >= home.arrival_h && (i === 0 || home.alerts[i - 1].hour < home.arrival_h);
          return (
            <Fragment key={a.hour}>
              {arrivesBefore && <ArrivalRow h={home.arrival_h} hour={hour} />}
              <li className={a.hour <= hour ? "" : "future"} style={{ "--c": LEVEL_COLOR[a.level] }}>
                <button className="hh-time num" onClick={() => onJump(a.hour)} title="Jump to this hour">T+{a.hour}h</button>
                <div>
                  <div className="hh-level">{LEVEL_LABEL[a.level]}
                    {lead != null && lead > 0 && <span className="num muted"> {lead.toFixed(1)} h before the water</span>}
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
      <span>Water 6 inches deep here (tide gauges)</span>
    </li>
  );
}

function DemoCard({ demo, state, hour, storm }) {
  const now = state?.now;
  const alerts = state?.alerts ?? [];
  const level = now?.level ?? "none";
  const first = (lvl) => alerts.find((a) => a.kind !== "water-arrived" && LEVELS.indexOf(a.level) >= LEVELS.indexOf(lvl));
  const lead = (a) => (a && demo.arrival_h != null ? demo.arrival_h - a.hour : null);
  const emailText = (e) => (e === "sent" ? "Email sent" : e === "sending" || e === "queued" ? "Sending email…" : `Email ${e}`);
  return (
    <div className="card demo-card">
      <div className="card-head"><h3>Alert demo</h3><span className="num muted">T+{hour}h</span></div>
      <p className="small-p muted">{storm?.info.name} ({new Date(storm?.info.start).getFullYear()}), replayed for this address with only the
        forecasts and gauge readings available at each hour.</p>
      <div className="demo-address">
        <span className="muted">Watching</span><b>{demo.label}</b>
        <span className="muted">Ground</span><b className="num">{demo.ground_ft != null ? `${demo.ground_ft.toFixed(1)} ft, water reaches it at ${demo.spill_ft.toFixed(1)} ft` : "high ground, water never reaches it"}</b>
        <span className="muted">Emails to</span><b>{demo.email || "nobody (set SENTINEL_ALERT_EMAIL)"}</b>
      </div>
      <div className="demo-level" style={{ "--c": LEVEL_COLOR[level] }}>
        <b>{LEVEL_LABEL[level]}</b>
        <span className="num">{now?.flooding_now_ft >= 0.5 ? `${now.flooding_now_ft.toFixed(1)} ft of water now`
          : now?.eta_h != null ? `Forecast to flood in ${Math.max(1, now.eta_h)} h, ${now.forecast_peak_depth_ft.toFixed(1)} ft at peak`
          : "No flooding forecast"}</span>
      </div>
      <ol className="hh-alerts">
        {alerts.length === 0 && <li className="hh-empty">Press play. Alerts appear here as the sentinel sends them.</li>}
        {alerts.map((a) => (
          <li key={a.id} style={{ "--c": a.kind === "water-arrived" ? "var(--ember)" : LEVEL_COLOR[a.level] }}>
            <span className="hh-time num">T+{(+a.hour).toFixed(a.kind === "water-arrived" ? 1 : 0)}h</span>
            <div>
              <div className="hh-level">{a.kind === "water-arrived" ? "Water at your address" : LEVEL_LABEL[a.level]}</div>
              <p>{a.message}</p>
              <span className={`email-chip ${a.email === "sent" ? "ok" : a.email.startsWith("not") ? "bad" : ""}`}>{emailText(a.email)}</span>
            </div>
          </li>
        ))}
      </ol>
      <div className="arrivals demo-sum">
        <div><b className="num">{demo.arrival_h == null ? "Stayed dry" : hour >= demo.arrival_h ? "Arrived" : `T+${demo.arrival_h.toFixed(1)}h`}</b><span>{demo.arrival_h == null ? "the water never reached it" : "Water reaches the address"}</span></div>
        <div><b className="num ok">{first("warn") && lead(first("warn")) != null ? `${lead(first("warn")).toFixed(1)} h` : "–"}</b><span>warning before the water</span></div>
        <div><b className="num ok">{first("evacuate") && lead(first("evacuate")) != null ? `${lead(first("evacuate")).toFixed(1)} h` : "–"}</b><span>told to leave before the water</span></div>
      </div>
      <p className="hint">Drag the timeline back to replay. Exit and click elsewhere to watch another address.</p>
    </div>
  );
}

function LiveBanner({ live }) {
  const f = live?.alerts?.features ?? [];
  const surge = f.filter((x) => /Storm Surge|Hurricane/.test(x.properties.event));
  const coastal = f.filter((x) => /Coastal Flood/.test(x.properties.event));
  const storms = live?.storms ?? [];
  if (!f.length && !storms.length) return null;
  const top = surge[0] ?? coastal[0];
  return (
    <div className="banner" role="status">
      <span className="banner-dot" />
      <b>{storms.length ? `${storms.length} active Atlantic storm${storms.length > 1 ? "s" : ""}` : top.properties.event}</b>
      <span>{top ? `${new Set(f.map((x) => x.properties.headline)).size} coastal alerts across ${new Set(f.map((x) => x.properties.state)).size} states` : ""}</span>
    </div>
  );
}

function LiveSide({ live, onFocus }) {
  if (!live?.gauges) {
    return (
      <div className="card">
        <h3>Live conditions</h3>
        <p className="small-p muted">Run <code>python -m surge live</code> in <code>modeling/</code> to load live data.</p>
      </div>
    );
  }
  const gauges = [...live.gauges].sort((a, b) => (b.anomaly_ft ?? -9) - (a.anomaly_ft ?? -9));
  const alerts = [];
  for (const f of live.alerts?.features ?? []) {
    const p = f.properties;
    let g = alerts.find((a) => a.headline === p.headline);
    if (!g) alerts.push(g = { ...p, zones: 0, geom: f.geometry });
    g.zones++;
  }
  const src = live.status?.sources || {};
  return (
    <>
      <div className="card">
        <div className="card-head"><h3>Atlantic storms</h3><span className="muted">NHC</span></div>
        {live.storms?.length ? (
          <ul className="rows">
            {live.storms.map((s) => (
              <li key={s.id}><button onClick={() => onFocus({ lon: s.lon, lat: s.lat, zoom: 5.5, t: Date.now() })}>
                <span>{s.name}</span><span className="num muted">{Math.round(s.vmax_kt * 1.15)} mph, {s.pmin_mb ?? "–"} mb</span>
              </button></li>
            ))}
          </ul>
        ) : <p className="small-p muted">No active Atlantic storms right now. The season runs June 1 to November 30; replay a past storm from the tabs above.</p>}
      </div>
      <div className="card">
        <div className="card-head"><h3>Water above the tide</h3><span className="muted">NOAA gauges, now</span></div>
        <ul className="gauge-list">
          {gauges.slice(0, 10).map((g) => (
            <li key={g.id}>
              <button onClick={() => onFocus({ lon: g.lon, lat: g.lat, zoom: 10, t: Date.now() })}>
                <span>{g.name}</span>
                <span className="gauge-bar" aria-hidden="true"><i style={{ width: `${Math.min(100, Math.max(2, ((g.anomaly_ft ?? 0) + 0.5) / 3 * 100))}%` }} /></span>
                <span className={`num ${g.anomaly_ft >= 1 ? "hot" : "muted"}`}>{g.anomaly_ft != null ? `${g.anomaly_ft >= 0 ? "+" : ""}${g.anomaly_ft.toFixed(1)} ft` : "–"}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="card">
        <div className="card-head"><h3>Coastal warnings</h3><span className="muted">NWS</span></div>
        {alerts.length ? (
          <ul className="scan-list">
            {alerts.slice(0, 6).map((a) => (
              <li key={a.headline}>
                <button style={{ "--c": /Warning/.test(a.event) ? "var(--ember)" : /Watch/.test(a.event) ? "var(--sky)" : "var(--flare)" }}
                        onClick={() => { const c = firstCoord(a.geom); if (c) onFocus({ lon: c[0], lat: c[1], zoom: 8, t: Date.now() }); }}>
                  <span className="scan-level">{a.event.replace("Coastal Flood ", "")}</span>
                  <span className="scan-fire">{a.event.startsWith("Coastal") ? "Coastal flooding" : a.event}</span>
                  <span className="num muted">{a.state}, {a.zones} zone{a.zones > 1 ? "s" : ""}{a.ends || a.expires ? `, until ${fmtTime(a.ends || a.expires, { month: undefined, day: undefined, weekday: "short" })}` : ""}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : <p className="small-p muted">No coastal flood, storm surge or hurricane alerts from Florida to Maine.</p>}
      </div>
      <div className="card">
        <div className="card-head"><h3>Feeds</h3></div>
        <ul className="feeds">
          {Object.entries(src).map(([k, v]) => (
            <li key={k}><span><i className={`status ${v.ok ? "ok" : "bad"}`} />{k.replace("_", " ")}</span>
              <span className="muted">{v.ok ? v.summary : "Unavailable"}</span></li>
          ))}
        </ul>
      </div>
    </>
  );
}

function firstCoord(g) {
  let c = g?.coordinates;
  while (Array.isArray(c) && Array.isArray(c[0])) c = c[0];
  return Array.isArray(c) ? c : null;
}

function Legend({ live }) {
  const items = live
    ? [["fill", "#ff5f6d", "Surge / hurricane warning"], ["fill", "#2fb7c9", "Coastal flood warning"], ["dot", "#ffc145", "Gauge 2 ft above tide"]]
    : [["dot", "var(--ember)", "Leave now"], ["dot", "var(--flare)", "Get ready"], ["ring", "#fff", "Later flooded"], ["fill", "#1aa6c4", "Flood water"], ["dash", "#e9f3f5", "Forecast cone"]];
  return (
    <div className="legend">
      {items.map(([kind, color, label]) => <span key={label}><i className={`lg-${kind}`} style={{ "--c": color }} />{label}</span>)}
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

function Scrubber({ hour, max, start, peak, onChange }) {
  const days = [];
  if (start) {
    const t0 = new Date(start);
    for (let h = 1; h <= max; h++) {
      const d = new Date(t0.getTime() + h * 3600e3);
      if (d.toLocaleString("en-US", { ...ET, hour: "numeric", hour12: false }) === "0") {
        days.push([h, d.toLocaleDateString("en-US", { ...ET, weekday: "short", day: "numeric" })]);
      }
    }
  }
  return (
    <div className="scrubber" style={{ "--p": `${(hour / max) * 100}%` }}>
      <input type="range" min={0} max={max} value={hour} aria-label="Hours into the replay" onChange={(e) => onChange(+e.target.value)} />
      <div className="scrub-track" aria-hidden="true"><div className="scrub-fill" /></div>
      {peak != null && <span className="scrub-peak" style={{ left: `${(peak / max) * 100}%` }} title="Peak water">Peak</span>}
      {days.map(([h, label]) => <span key={h} className="scrub-day" style={{ left: `${(h / max) * 100}%` }}>{label}</span>)}
    </div>
  );
}

function Metric({ v, u, l, hot, c }) {
  return (
    <div className={`metric ${hot ? "hot" : ""}`} style={c ? { "--mc": c } : undefined}>
      <b className="num" style={c ? { color: c } : undefined}>{v}<small>{u}</small></b>
      <span>{l}</span>
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
