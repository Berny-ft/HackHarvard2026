import { useEffect, useRef } from "react";
import mapboxgl from "mapbox-gl";
import { WindAnimator } from "../wind.js";
import { SurgeRenderer } from "../water.js";
import { BASEMAPS } from "../MapView.jsx";

mapboxgl.accessToken = import.meta.env.VITE_MAPBOX_TOKEN;

const DATA = "/data/surge";
const EMPTY = { type: "FeatureCollection", features: [] };

export const CAMERAS = {
  "3d": { label: "3D", pitch: 58 },
  "2d": { label: "2D", pitch: 0, bearing: 0 },
  ground: { label: "Street", pitch: 78, zoomDelta: 1.3 },
  region: { label: "Storm", pitch: 30 },
};
const COAST = { center: [-76.2, 35.2], zoom: 4.7, pitch: 20, bearing: 0 };

export const LAYER_GROUPS = {
  track: ["track-line", "track-points"],
  cone: ["cone-fill", "cone-line", "forecast-points"],
  radii: ["radii-fill", "radii-line"],
  gauges: ["gauges", "gauge-labels"],
  households: ["households-halo", "households"],
  extent: ["extent-line"],
  liveGauges: ["live-gauges", "live-gauge-labels"],
  liveAlerts: ["alert-fill", "alert-line"],
  liveStorms: ["live-storms", "live-storm-labels"],
};
const LIVE_KEYS = new Set(["liveGauges", "liveAlerts", "liveStorms"]);
const STORM_KEYS = new Set(["track", "cone", "radii", "gauges", "households", "extent"]);
// Saffir-Simpson colours: depression/storm in sea-glass, hurricanes from amber to violet.
export const CAT_COLOR = ["match", ["get", "cat"], -1, "#7fb6c2", 0, "#9fd8cb", 1, "#ffd166", 2, "#ffa24c", 3, "#ff6b5b", 4, "#e8437a", 5, "#b14be0", "#7fb6c2"];
export const CAT_HEX = { "-1": "#7fb6c2", 0: "#9fd8cb", 1: "#ffd166", 2: "#ffa24c", 3: "#ff6b5b", 4: "#e8437a", 5: "#b14be0" };
const LEVEL_PAINT = ["match", ["get", "level"], "evacuate", "#ff5f6d", "warn", "#ffc145", "watch", "#7cc7ff", "#5f7d8c"];
const LABEL_PAINT = { "text-color": "#fff", "text-halo-color": "rgba(2,10,20,0.85)", "text-halo-width": 1.4 };
const ALERT_COLOR = ["match", ["get", "event"],
  "Storm Surge Warning", "#ff5f6d", "Storm Surge Watch", "#ff9aa3", "Hurricane Warning", "#e8437a", "Hurricane Watch", "#ff8fb8",
  "Tropical Storm Warning", "#ffa24c", "Tropical Storm Watch", "#ffd166", "Coastal Flood Warning", "#2fb7c9",
  "Coastal Flood Advisory", "#7fd6c9", "Coastal Flood Watch", "#5ba4d6", "#8fb3c9"];

async function getJson(path, fallback = EMPTY) {
  try {
    const r = await fetch(`${DATA}/${path}`);
    return r.ok ? await r.json() : fallback;
  } catch {
    return fallback;
  }
}

async function loadStatic() {
  const [gauges, alerts, storms] = await Promise.all([
    getJson("live/gauges.json", []), getJson("live/alerts.geojson"), getJson("live/storms.json", []),
  ]);
  const pts = (rows, props) => ({ type: "FeatureCollection", features: (rows || []).map((r) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [r.lon, r.lat] }, properties: props(r) })) });
  return {
    alerts,
    gauges: pts(gauges, (g) => ({ id: g.id, anomaly: g.anomaly_ft ?? 0,
      label: `${g.name.split(",")[0]} ${g.anomaly_ft != null ? `${g.anomaly_ft >= 0 ? "+" : ""}${g.anomaly_ft.toFixed(1)} ft` : ""}` })),
    storms: pts(storms, (s) => ({ cat: catOf(s.vmax_kt), label: `${s.name} · ${Math.round(s.vmax_kt * 1.15)} mph` })),
  };
}

export function catOf(kt) {
  for (const [c, v] of [[5, 137], [4, 113], [3, 96], [2, 83], [1, 64]]) if (kt >= v) return c;
  return kt >= 34 ? 0 : -1;
}

// --- geometry helpers ------------------------------------------------------

function destination(lon, lat, km, deg) {
  const a = (deg * Math.PI) / 180;
  return [lon + (km * Math.sin(a)) / (111.32 * Math.cos((lat * Math.PI) / 180)), lat + (km * Math.cos(a)) / 110.57];
}

/** Wind-radii quadrants (nm: NE, SE, SW, NW) -> polygon ring. */
function radiiRing(lon, lat, quads) {
  const ring = [];
  for (let q = 0; q < 4; q++) {
    const km = (quads[q] || 0) * 1.852;
    for (let d = q * 90; d <= q * 90 + 90; d += 6) ring.push(destination(lon, lat, km, d));
  }
  ring.push(ring[0]);
  return ring;
}

export function stormState(track, t) {
  if (!track?.length || t < track[0].t || t > track[track.length - 1].t) return null;
  for (let i = 0; i < track.length - 1; i++) {
    const a = track[i], b = track[i + 1];
    if (t >= a.t && t <= b.t) {
      const f = b.t === a.t ? 0 : (t - a.t) / (b.t - a.t);
      return { lat: a.lat + (b.lat - a.lat) * f, lon: a.lon + (b.lon - a.lon) * f,
               vmax_kt: a.vmax_kt + (b.vmax_kt - a.vmax_kt) * f, pmin_mb: a.pmin_mb + (b.pmin_mb - a.pmin_mb) * f,
               radii: (f < 0.5 ? a : b).radii, cat: catOf(a.vmax_kt + (b.vmax_kt - a.vmax_kt) * f) };
    }
  }
  return null;
}

function trackGeo(track) {
  const feats = [];
  for (let i = 0; i < track.length - 1; i++) {
    const a = track[i], b = track[i + 1];
    feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: [[a.lon, a.lat], [b.lon, b.lat]] },
                 properties: { t: a.t, cat: a.cat } });
  }
  return { type: "FeatureCollection", features: feats };
}

function trackPoints(track) {
  return { type: "FeatureCollection", features: track.filter((p) => Math.abs(p.t % 6) < 0.01).map((p) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] }, properties: { t: p.t, cat: p.cat } })) };
}

function advisoryAt(advisories, hour) {
  let a = null;
  for (const f of advisories?.features ?? []) if (f.properties.issued_h <= hour) a = f;
  return a;
}

function nowGeo(storm, hour) {
  const st = stormState(storm.info.best_track, hour);
  const adv = advisoryAt(storm.advisories, hour);
  const radii = [];
  if (st) {
    for (const k of ["34", "50", "64"]) {
      if (st.radii?.[k]) radii.push({ type: "Feature", geometry: { type: "Polygon", coordinates: [radiiRing(st.lon, st.lat, st.radii[k])] },
                                      properties: { kt: +k } });
    }
  }
  const gauges = storm.info.field_gauges.concat(Object.keys(storm.gauges).filter((g) => !storm.info.field_gauges.includes(g)))
    .map((id) => {
      const g = storm.gauges[id];
      const v = g.observed_ft[Math.min(hour, g.observed_ft.length - 1)];
      return { type: "Feature", geometry: { type: "Point", coordinates: [g.lon, g.lat] },
               properties: { label: `${g.name.split(",")[0]}  ${v != null ? `${v.toFixed(1)} ft` : "offline"}`,
                             surge: g.surge_ft[Math.min(hour, g.surge_ft.length - 1)] ?? 0, offline: v == null } };
    });
  return {
    storm: st ? { type: "FeatureCollection", features: [{ type: "Feature", geometry: { type: "Point", coordinates: [st.lon, st.lat] },
      properties: { cat: st.cat, label: `${storm.info.name.replace("Hurricane ", "")} · ${Math.round(st.vmax_kt * 1.15)} mph` } }] } : EMPTY,
    radii: { type: "FeatureCollection", features: radii },
    cone: adv ? { type: "FeatureCollection", features: [{ ...adv, properties: { issued_h: adv.properties.issued_h } }] } : EMPTY,
    forecast: adv ? { type: "FeatureCollection", features: adv.properties.points.filter((p) => [12, 24, 36, 48, 72, 96, 120].includes(p.tau)).map((p) => ({
      type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] }, properties: { cat: p.cat, label: `${p.tau}h` } })) } : EMPTY,
    gauges: { type: "FeatureCollection", features: gauges },
  };
}

// --- layers ----------------------------------------------------------------

function install(map, s, isStandard, water) {
  const add = (layer, slot = "middle") => {
    const l = { ...layer, paint: { ...(layer.paint || {}) } };
    if (isStandard) {
      l.slot = slot;
      const glow = { fill: "fill-emissive-strength", line: "line-emissive-strength", circle: "circle-emissive-strength",
                     symbol: "text-emissive-strength" }[l.type];
      if (glow) l.paint[glow] = 1;
    }
    map.addLayer(l);
  };
  if (!map.getSource("mapbox-dem")) {
    map.addSource("mapbox-dem", { type: "raster-dem", url: "mapbox://mapbox.mapbox-terrain-dem-v1", tileSize: 512, maxzoom: 14 });
  }
  map.setTerrain({ source: "mapbox-dem", exaggeration: 1.2 });

  map.addSource("alerts", { type: "geojson", data: s.alerts });
  add({ id: "alert-fill", type: "fill", source: "alerts", paint: { "fill-color": ALERT_COLOR, "fill-opacity": 0.18 } }, "bottom");
  add({ id: "alert-line", type: "line", source: "alerts", paint: { "line-color": ALERT_COLOR, "line-width": 1.2 } });

  for (const id of ["cone", "radii", "track", "track-pts", "storm-now", "forecast", "gauges", "extent"]) {
    map.addSource(id, { type: "geojson", data: EMPTY });
  }
  // Storm-scale fills fade out as you zoom into the city, where they would only tint everything.
  const FADE = ["interpolate", ["linear"], ["zoom"], 5, 0.12, 8, 0.05, 10, 0];
  add({ id: "cone-fill", type: "fill", source: "cone", paint: { "fill-color": "#e9f3f5", "fill-opacity": FADE } }, "bottom");
  add({ id: "cone-line", type: "line", source: "cone", paint: { "line-color": "#e9f3f5", "line-width": 1.2, "line-dasharray": [2, 2], "line-opacity": 0.7 } });
  add({ id: "radii-fill", type: "fill", source: "radii",
        paint: { "fill-color": ["match", ["get", "kt"], 64, "#ff6b5b", 50, "#ffa24c", "#ffd166"], "fill-opacity": FADE } }, "bottom");
  add({ id: "radii-line", type: "line", source: "radii",
        paint: { "line-color": ["match", ["get", "kt"], 64, "#ff6b5b", 50, "#ffa24c", "#ffd166"], "line-width": 1, "line-opacity": ["interpolate", ["linear"], ["zoom"], 7, 0.6, 10, 0.15] } });

  // Modeled water: shader flood on the terrain + three.js hurricane cloud and rain.
  water?.attach(isStandard);

  add({ id: "extent-line", type: "line", source: "extent",
        paint: { "line-color": "#e9f7ff", "line-width": 1.4, "line-dasharray": [2, 1.6], "line-opacity": 0.85 } });
  add({ id: "track-line", type: "line", source: "track", layout: { "line-cap": "round" },
        paint: { "line-color": CAT_COLOR, "line-width": ["interpolate", ["linear"], ["zoom"], 4, 2, 10, 4] } }, "top");
  add({ id: "track-points", type: "circle", source: "track-pts",
        paint: { "circle-radius": 3, "circle-color": CAT_COLOR, "circle-stroke-color": "#04121c", "circle-stroke-width": 1 } }, "top");
  add({ id: "forecast-points", type: "circle", source: "forecast",
        paint: { "circle-radius": 4, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": CAT_COLOR, "circle-stroke-width": 1.6 } }, "top");
  add({ id: "forecast-labels", type: "symbol", source: "forecast",
        layout: { "text-field": ["get", "label"], "text-size": 10, "text-offset": [0, 1.1], "text-anchor": "top" }, paint: LABEL_PAINT }, "top");

  add({ id: "storm-halo", type: "circle", source: "storm-now",
        paint: { "circle-radius": 18, "circle-color": CAT_COLOR, "circle-opacity": 0.2, "circle-stroke-color": CAT_COLOR,
                 "circle-stroke-width": 2 } }, "top");
  add({ id: "storm-dot", type: "circle", source: "storm-now",
        paint: { "circle-radius": 6, "circle-color": CAT_COLOR, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } }, "top");
  add({ id: "storm-label", type: "symbol", source: "storm-now",
        layout: { "text-field": ["get", "label"], "text-size": 12, "text-offset": [0, 1.9], "text-anchor": "top", "text-allow-overlap": true },
        paint: LABEL_PAINT }, "top");

  add({ id: "gauges", type: "circle", source: "gauges",
        paint: { "circle-radius": 6, "circle-color": ["case", ["get", "offline"], "#5f7d8c",
                   ["interpolate", ["linear"], ["get", "surge"], 0, "#9fd8cb", 3, "#2fb7c9", 6, "#7c5cff", 9, "#e8437a"]],
                 "circle-stroke-color": "#fff", "circle-stroke-width": 1.8 } }, "top");
  add({ id: "gauge-labels", type: "symbol", source: "gauges",
        layout: { "text-field": ["get", "label"], "text-size": 11, "text-offset": [0, 1.2], "text-anchor": "top" }, paint: LABEL_PAINT }, "top");

  map.addSource("households", { type: "geojson", data: EMPTY });
  add({ id: "households-halo", type: "circle", source: "households", filter: ["==", ["get", "selected"], true],
        paint: { "circle-radius": 16, "circle-color": "#7ff0d0", "circle-opacity": 0.18, "circle-stroke-color": "#7ff0d0", "circle-stroke-width": 1.5 } }, "top");
  add({ id: "households", type: "circle", source: "households",
        paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.6, 12, 4.5, 15, 8], "circle-color": LEVEL_PAINT,
                 "circle-stroke-color": ["case", ["get", "flooded"], "#ffffff", "#03121c"],
                 "circle-stroke-width": ["case", ["get", "flooded"], 1.4, 0.8] } }, "top");

  map.addSource("live-gauges", { type: "geojson", data: s.gauges });
  add({ id: "live-gauges", type: "circle", source: "live-gauges",
        paint: { "circle-radius": 6, "circle-color": ["interpolate", ["linear"], ["get", "anomaly"], -1, "#7cc7ff", 0, "#9fd8cb", 1, "#2fb7c9", 2, "#ffc145", 3, "#ff5f6d"],
                 "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } }, "top");
  add({ id: "live-gauge-labels", type: "symbol", source: "live-gauges",
        layout: { "text-field": ["get", "label"], "text-size": 10.5, "text-offset": [0, 1.2], "text-anchor": "top" }, paint: LABEL_PAINT }, "top");
  map.addSource("live-storms", { type: "geojson", data: s.storms });
  add({ id: "live-storms", type: "circle", source: "live-storms",
        paint: { "circle-radius": 9, "circle-color": CAT_COLOR, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } }, "top");
  add({ id: "live-storm-labels", type: "symbol", source: "live-storms",
        layout: { "text-field": ["get", "label"], "text-size": 12, "text-offset": [0, 1.5], "text-anchor": "top" }, paint: LABEL_PAINT }, "top");

  map.addSource("demo-home", { type: "geojson", data: EMPTY });
  add({ id: "demo-home-halo", type: "circle", source: "demo-home",
        paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 14, 14, 34], "circle-color": LEVEL_PAINT, "circle-opacity": 0.22,
                 "circle-stroke-color": LEVEL_PAINT, "circle-stroke-width": 2, "circle-pitch-alignment": "map" } }, "top");
  add({ id: "demo-home", type: "circle", source: "demo-home",
        paint: { "circle-radius": 8, "circle-color": LEVEL_PAINT, "circle-stroke-color": "#fff", "circle-stroke-width": 2.5 } }, "top");
  add({ id: "demo-home-label", type: "symbol", source: "demo-home",
        layout: { "text-field": ["get", "label"], "text-size": 13, "text-offset": [0, 1.6], "text-anchor": "top", "text-allow-overlap": true },
        paint: LABEL_PAINT }, "top");
}

function setStormData(map, storm) {
  if (!storm) {
    for (const id of ["track", "track-pts", "extent", "storm-now", "radii", "cone", "forecast", "gauges"]) map.getSource(id)?.setData(EMPTY);
    return;
  }
  map.getSource("track").setData(trackGeo(storm.info.best_track));
  map.getSource("track-pts").setData(trackPoints(storm.info.best_track));
  map.getSource("extent").setData(storm.extent);
}

function setTime(map, storm, hour) {
  if (!storm) return;
  const g = nowGeo(storm, hour);
  map.getSource("storm-now").setData(g.storm);
  map.getSource("radii").setData(g.radii);
  map.getSource("cone").setData(g.cone);
  map.getSource("forecast").setData(g.forecast);
  map.getSource("gauges").setData(g.gauges);
  // Track: the past solid, the rest faint.
  map.setPaintProperty("track-line", "line-opacity", ["case", ["<=", ["get", "t"], hour], 1, 0.25]);
  map.setPaintProperty("track-points", "circle-opacity", ["case", ["<=", ["get", "t"], hour], 1, 0.25]);
  map.setPaintProperty("track-points", "circle-stroke-opacity", ["case", ["<=", ["get", "t"], hour], 1, 0.25]);
}

function setVisibility(map, live, layers, water) {
  water?.setVisible(!live && !!layers.water, !!layers.sky);
  for (const [key, ids] of Object.entries(LAYER_GROUPS)) {
    const scoped = LIVE_KEYS.has(key) ? live : STORM_KEYS.has(key) ? !live : true;
    for (const id of ids) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", layers[key] && scoped ? "visible" : "none");
  }
  for (const id of ["storm-halo", "storm-dot", "storm-label", "forecast-labels"]) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", live ? "none" : (id === "forecast-labels" ? (layers.cone ? "visible" : "none") : "visible"));
  }
}

export function flyTo(map, storm, camera, home, hour = 0, duration = 2500) {
  const cam = CAMERAS[camera] ?? CAMERAS["3d"];
  if (!storm) { map.flyTo({ ...COAST, duration }); return; }
  if (camera === "region") {
    const st = stormState(storm.info.best_track, hour) ?? storm.info.best_track[0];
    const c = storm.info.center;
    map.flyTo({ center: [(st.lon + c.lon) / 2, (st.lat + c.lat) / 2], zoom: 5.2, pitch: cam.pitch, bearing: 0, duration });
    return;
  }
  const c = home ?? storm.info.center;
  map.flyTo({ center: [c.lon, c.lat - (cam.pitch > 40 ? 0.03 : 0)], zoom: 11 + (cam.zoomDelta || 0) + (home ? 1 : 0), pitch: cam.pitch,
              bearing: cam.bearing ?? storm.info.camera?.bearing ?? 0, duration });
}

function applyWind(wind, s, stat, water) {
  if (!wind) return;
  const dark = s.basemap === "satellite" || s.basemap === "dark" ||
    (BASEMAPS[s.basemap].standard && (s.light === "dusk" || s.light === "night"));
  water?.setTheme(BASEMAPS[s.basemap].standard ? s.light === "dusk" || s.light === "night" : s.basemap === "dark");
  wind.setTheme(dark);
  // The wind is the city's (one weather series), so it only shows once you are zoomed into the city.
  wind.setVisible(!!s.layers.wind && !s.live && (s.zoom ?? 11) > 8);
  if (!s.live && s.storm) {
    const r = s.storm.series[Math.min(s.hour, s.storm.series.length - 1)];
    wind.setUniform(r.wind_dir_deg, r.wind_mph, r.gust_mph);
    water?.setWind(r.wind_dir_deg, r.wind_mph);
  }
}

function homeGeo(home) {
  if (!home) return EMPTY;
  return { type: "FeatureCollection", features: [{ type: "Feature", geometry: { type: "Point", coordinates: [home.lon, home.lat] },
    properties: { level: home.level, label: home.label } }] };
}

export default function SurgeMapView({ live, storm, hour, playRate = 0, layers, households, home, basemap, light, camera, focus,
                                       onSelectHousehold, onPick, picking }) {
  const el = useRef(null);
  const canvasRef = useRef(null);
  const windRef = useRef(null);
  const waterRef = useRef(null);
  const mapRef = useRef(null);
  const staticRef = useRef(null);
  const ready = useRef(false);
  const state = useRef({});
  state.current = { ...state.current, live, storm, hour, layers, households, home, basemap, light, camera, onSelectHousehold, onPick, picking };

  const restore = (map) => {
    const s = state.current;
    if (BASEMAPS[s.basemap].standard) map.setConfigProperty("basemap", "lightPreset", s.light);
    install(map, staticRef.current, !!BASEMAPS[s.basemap].standard, waterRef.current);
    setStormData(map, s.live ? null : s.storm);
    waterRef.current.setData(s.live ? null : s.storm);
    waterRef.current.setHour(s.hour);
    setTime(map, s.live ? null : s.storm, s.hour);
    map.getSource("households").setData(s.live ? EMPTY : s.households || EMPTY);
    map.getSource("demo-home").setData(homeGeo(s.home));
    setVisibility(map, s.live, s.layers, waterRef.current);
    applyWind(windRef.current, s, staticRef.current, waterRef.current);
  };

  useEffect(() => {
    if (!mapboxgl.accessToken) return;
    ready.current = false;
    const map = new mapboxgl.Map({
      container: el.current, style: BASEMAPS[basemap].style, projection: "mercator",
      config: BASEMAPS[basemap].standard ? { basemap: { lightPreset: light } } : undefined,
      ...COAST, maxPitch: 85, keyboard: false,   // arrows step the replay instead
    });
    mapRef.current = map;
    windRef.current = new WindAnimator(map, canvasRef.current);
    waterRef.current = new SurgeRenderer(map);
    if (import.meta.env.DEV) Object.assign(window, { __map: map, __water: waterRef.current });
    map.addControl(new mapboxgl.NavigationControl({ visualizePitch: true }), "bottom-right");
    map.addControl(new mapboxgl.ScaleControl({ unit: "imperial" }), "bottom-right");
    map.on("click", (e) => {
      const s = state.current;
      if (s.picking) { s.onPick?.({ lon: e.lngLat.lng, lat: e.lngLat.lat }); return; }
      if (!map.getLayer("households")) return;
      const hit = map.queryRenderedFeatures(e.point, { layers: ["households"] });
      s.onSelectHousehold?.(hit.length ? hit[0].properties.idx : null);
    });
    map.on("zoomend", () => {
      state.current.zoom = map.getZoom();
      applyWind(windRef.current, state.current, staticRef.current, waterRef.current);
    });
    map.on("mouseenter", "households", () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "households", () => { map.getCanvas().style.cursor = state.current.picking ? "crosshair" : ""; });

    let first = true;
    map.on("style.load", async () => {
      if (!staticRef.current) staticRef.current = await loadStatic();
      restore(map);
      ready.current = true;
      if (first) {
        first = false;
        const s = state.current;
        flyTo(map, s.live ? null : s.storm, s.camera, s.home, s.hour);
      }
    });
    return () => {
      ready.current = false;
      windRef.current?.destroy();
      waterRef.current?.destroy();
      map.remove();
    };
  }, []);

  useEffect(() => {
    if (mapRef.current) mapRef.current.getCanvas().style.cursor = picking ? "crosshair" : "";
  }, [picking]);

  useEffect(() => {
    applyWind(windRef.current, state.current, staticRef.current, waterRef.current);
  }, [live, storm, hour, layers, basemap, light]);

  const lastBasemap = useRef(basemap);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || lastBasemap.current === basemap) return;
    lastBasemap.current = basemap;
    ready.current = false;
    map.setStyle(BASEMAPS[basemap].style);
  }, [basemap]);

  useEffect(() => {
    if (ready.current && BASEMAPS[basemap].standard) mapRef.current.setConfigProperty("basemap", "lightPreset", light);
  }, [light]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready.current) return;
    setStormData(map, live ? null : storm);
    waterRef.current.setData(live ? null : storm);
    setTime(map, live ? null : storm, hour);
    setVisibility(map, live, layers, waterRef.current);
    flyTo(map, live ? null : storm, camera, home, hour);
  }, [live, storm]);

  useEffect(() => {
    if (ready.current) flyTo(mapRef.current, live ? null : storm, camera, state.current.home, hour, 1800);
  }, [camera]);

  useEffect(() => {
    waterRef.current?.setHour(hour, playRate);
    if (ready.current) setTime(mapRef.current, live ? null : storm, hour);
  }, [hour, storm, playRate]);

  useEffect(() => {
    if (ready.current) mapRef.current.getSource("households")?.setData(live ? EMPTY : households || EMPTY);
  }, [households, live]);

  useEffect(() => {
    if (ready.current) mapRef.current.getSource("demo-home")?.setData(homeGeo(home));
  }, [home?.lat, home?.lon, home?.level, home?.label]);

  useEffect(() => {
    if (focus && ready.current) mapRef.current.flyTo({ center: [focus.lon, focus.lat], zoom: focus.zoom ?? 10.5, pitch: 50, duration: 2200, essential: true });
  }, [focus]);

  useEffect(() => {
    if (ready.current) setVisibility(mapRef.current, live, layers, waterRef.current);
  }, [layers, live]);

  if (!mapboxgl.accessToken) {
    return (
      <div className="map notoken">
        <div>
          <b>Mapbox token missing.</b>
          <p>Add <code>VITE_MAPBOX_TOKEN=pk...</code> to the project-root <code>.env.local</code> and restart <code>npm run dev</code>.</p>
        </div>
      </div>
    );
  }
  return (
    <>
      <div ref={el} className="map" />
      <canvas ref={canvasRef} className="wind" />
    </>
  );
}
