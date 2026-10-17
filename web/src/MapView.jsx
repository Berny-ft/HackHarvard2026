import { useEffect, useRef } from "react";
import mapboxgl from "mapbox-gl";

mapboxgl.accessToken = import.meta.env.VITE_MAPBOX_TOKEN;

const DATA = "/data";
const EMPTY = { type: "FeatureCollection", features: [] };

export const BASEMAPS = {
  standard: { label: "Standard", style: "mapbox://styles/mapbox/standard", standard: true },
  satellite: { label: "Satellite", style: "mapbox://styles/mapbox/standard-satellite", standard: true },
  terrain: { label: "Terrain", style: "mapbox://styles/mapbox/outdoors-v12" },
  dark: { label: "Dark", style: "mapbox://styles/mapbox/dark-v11" },
};
export const LIGHTS = ["day", "dawn", "dusk", "night"];
export const CAMERAS = {
  "3d": { label: "3D", pitch: 62 },
  "2d": { label: "2D", pitch: 0, bearing: 0 },
  ground: { label: "Ground", pitch: 80, zoomDelta: 0.8 },
  overview: { label: "Region", center: [-118.55, 34.15], zoom: 8.4, pitch: 35, bearing: -10 },
};

// Which map layers each panel toggle controls.
export const LAYER_GROUPS = {
  risk: ["risk"],
  timesBurned: ["times-burned"],
  history: ["history-fill", "history-line"],
  spread: ["perim-fill", "perim-rings", "perim-front"],
  real: ["real-line"],
  structures: ["structures"],
  detections: ["detections"],
  redFlags: ["redflag-fill", "redflag-line"],
  santaAna: ["wx-points", "wx-labels"],
  powerLines: ["power-lines"],
  activeFires: ["active-perims", "active-incidents", "active-labels"],
  hotspots: ["hotspots"],
};
const LIVE_KEYS = new Set(["redFlags", "santaAna", "powerLines", "activeFires", "hotspots"]);
const INCIDENT_KEYS = new Set(["spread", "real", "structures", "detections"]);
const INCIDENT_SOURCES = ["perims", "real", "structures", "ignition", "detections"];

const SA_COLOR = ["match", ["get", "level"], 2, "#7a0f0f", 1, "#d9482b", "#6b8e3a"];
const LABEL_PAINT = { "text-color": "#fff", "text-halo-color": "rgba(0,0,0,0.75)", "text-halo-width": 1.4 };

async function getJson(path, fallback = EMPTY) {
  try {
    const r = await fetch(`${DATA}/${path}`);
    return r.ok ? await r.json() : fallback;
  } catch {
    return fallback;
  }
}

function points(rows, props) {
  return {
    type: "FeatureCollection",
    features: (Array.isArray(rows) ? rows : []).map((r) => ({
      type: "Feature", geometry: { type: "Point", coordinates: [r.lon, r.lat] }, properties: props(r),
    })),
  };
}

// Everything that doesn't change with the incident: fetched once, re-added per style.
async function loadStatic() {
  const [risk, timesBurned, history, redFlags, weather, power, perims, incidents, hot] = await Promise.all([
    getJson("burn_probability.json", null), getJson("times_burned.json", null), getJson("fire_history.geojson"),
    getJson("live/red_flags.geojson"), getJson("live/weather.json", []), getJson("live/power_lines.geojson"),
    getJson("live/perimeters.geojson"), getJson("live/incidents.json", []), getJson("live/hotspots.geojson"),
  ]);
  return {
    risk, timesBurned, history, redFlags, power, perims, hot,
    weather: points(weather, (r) => ({
      level: Math.max(r.santa_ana_level_now, r.santa_ana_level_peak_48h),
      label: `${Math.round(r.max_gust_48h_mph)} mph · ${r.min_fuel_moisture_48h_pct}%`,
    })),
    incidents: points(incidents, (r) => ({
      label: `${r.name}${r.acres ? ` · ${Math.round(r.acres).toLocaleString()} ac` : ""}${r.contained_pct != null ? ` · ${r.contained_pct}%` : ""}`,
      acres: r.acres || 0,
    })),
  };
}

function install(map, s, isStandard) {
  // Standard styles place custom layers in slots and support emissive (glow at night).
  const add = (layer, slot = "middle") => {
    const l = { ...layer, paint: { ...(layer.paint || {}) } };
    if (isStandard) {
      l.slot = slot;
      const glow = { fill: "fill-emissive-strength", line: "line-emissive-strength", circle: "circle-emissive-strength",
                     raster: "raster-emissive-strength", symbol: "text-emissive-strength" }[l.type];
      if (glow) l.paint[glow] = 1;
    }
    map.addLayer(l);
  };

  if (!map.getSource("mapbox-dem")) {
    map.addSource("mapbox-dem", { type: "raster-dem", url: "mapbox://mapbox.mapbox-terrain-dem-v1", tileSize: 512, maxzoom: 14 });
  }
  map.setTerrain({ source: "mapbox-dem", exaggeration: 1.35 });

  for (const [id, meta] of [["risk", s.risk], ["times-burned", s.timesBurned]]) {
    if (!meta) continue;
    map.addSource(id, { type: "image", url: `${DATA}/${meta.image}`, coordinates: meta.coordinates });
    add({ id, type: "raster", source: id, paint: { "raster-opacity": 0.75, "raster-fade-duration": 0 } }, "bottom");
  }

  map.addSource("history", { type: "geojson", data: s.history });
  add({
    id: "history-fill", type: "fill", source: "history",
    paint: {
      "fill-color": ["interpolate", ["linear"], ["get", "year"], 1950, "#f3d38a", 2000, "#e8873a", 2025, "#9e1f1a"],
      "fill-opacity": 0.2,
    },
  }, "bottom");
  add({ id: "history-line", type: "line", source: "history",
        paint: { "line-color": "#c9772e", "line-width": 0.6, "line-opacity": 0.6 } }, "bottom");

  map.addSource("power", { type: "geojson", data: s.power });
  add({ id: "power-lines", type: "line", source: "power",
        paint: { "line-color": "#ffd23f", "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.4, 13, 1.6], "line-opacity": 0.75 } });
  map.addSource("redflags", { type: "geojson", data: s.redFlags });
  add({ id: "redflag-fill", type: "fill", source: "redflags", paint: { "fill-color": "#e0245e", "fill-opacity": 0.15 } }, "bottom");
  add({ id: "redflag-line", type: "line", source: "redflags", paint: { "line-color": "#e0245e", "line-width": 1.5 } });
  map.addSource("active-perims", { type: "geojson", data: s.perims });
  add({ id: "active-perims", type: "fill", source: "active-perims",
        paint: { "fill-color": "#ff3b1f", "fill-opacity": 0.45, "fill-outline-color": "#ffcf4a" } });

  for (const id of INCIDENT_SOURCES) map.addSource(id, { type: "geojson", data: EMPTY });
  add({ id: "perim-fill", type: "fill", source: "perims", paint: { "fill-color": "#e5482a", "fill-opacity": 0.42 } });
  add({ id: "perim-rings", type: "line", source: "perims",
        paint: { "line-color": ["interpolate", ["linear"], ["get", "hour"], 0, "#ffe27a", 36, "#f08a2c", 72, "#b3261e"],
                 "line-width": 1, "line-opacity": 0.85 } });
  add({ id: "perim-front", type: "line", source: "perims", paint: { "line-color": "#ffcf4a", "line-width": 3, "line-blur": 1 } });
  add({ id: "real-line", type: "line", source: "real",
        paint: { "line-color": "#ffffff", "line-width": 2, "line-dasharray": [2, 1.5] } });
  add({
    id: "structures", type: "circle", source: "structures",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 1.2, 15, 4],
      "circle-color": ["match", ["slice", ["get", "DAMAGE"], 0, 5],
        "Destr", "#b3261e", "Major", "#e5482a", "Minor", "#f08a2c", "Affec", "#f5c04a", "#7fbf7a"],
      "circle-stroke-color": "#1d1d1d", "circle-stroke-width": 0.3,
    },
  }, "top");

  map.addSource("hotspots", { type: "geojson", data: s.hot });
  for (const [id, src] of [["hotspots", "hotspots"], ["detections", "detections"]]) {
    add({
      id, type: "circle", source: src,
      paint: {
        "circle-radius": id === "hotspots" ? ["interpolate", ["linear"], ["get", "frp_mw"], 0, 3, 100, 9]
                                           : ["interpolate", ["linear"], ["zoom"], 9, 2, 14, 7],
        "circle-color": "#ffb000", "circle-stroke-color": "#5a1a00", "circle-stroke-width": 0.8,
      },
    }, "top");
  }

  map.addSource("wx", { type: "geojson", data: s.weather });
  add({ id: "wx-points", type: "circle", source: "wx",
        paint: { "circle-radius": 7, "circle-color": SA_COLOR, "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } }, "top");
  add({ id: "wx-labels", type: "symbol", source: "wx",
        layout: { "text-field": ["get", "label"], "text-size": 10, "text-offset": [0, 1.3], "text-anchor": "top" },
        paint: LABEL_PAINT }, "top");
  map.addSource("active-incidents", { type: "geojson", data: s.incidents });
  add({ id: "active-incidents", type: "circle", source: "active-incidents",
        paint: { "circle-radius": ["interpolate", ["linear"], ["get", "acres"], 0, 4, 1000, 9],
                 "circle-color": "#ff3b1f", "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } }, "top");
  add({ id: "active-labels", type: "symbol", source: "active-incidents", filter: [">=", ["get", "acres"], 10],
        layout: { "text-field": ["get", "label"], "text-size": 11, "text-offset": [0, 1.2], "text-anchor": "top" },
        paint: LABEL_PAINT }, "top");

  add({ id: "ignition", type: "circle", source: "ignition",
        paint: { "circle-radius": 7, "circle-color": "#ff5a36", "circle-stroke-color": "#fff", "circle-stroke-width": 2 } }, "top");
  add({ id: "ignition-label", type: "symbol", source: "ignition",
        layout: { "text-field": ["get", "label"], "text-size": 12, "text-offset": [0, 1.4], "text-anchor": "top" },
        paint: LABEL_PAINT }, "top");
}

function setIncidentData(map, incident, data) {
  if (incident === "live" || !data) {
    for (const id of INCIDENT_SOURCES) map.getSource(id)?.setData(EMPTY);
    return;
  }
  map.getSource("perims").setData(data.perimeters);
  map.getSource("real").setData(data.real);
  map.getSource("structures").setData(data.structures);
  map.getSource("detections").setData(data.detections);
  const { lon, lat } = data.info.ignition;
  const when = new Date(data.info.start).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  map.getSource("ignition").setData({
    type: "FeatureCollection",
    features: [{ type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties: { label: `Ignition · ${when}` } }],
  });
}

function setTime(map, hour) {
  const h = Math.max(1, hour);
  map.setFilter("perim-fill", ["==", ["get", "hour"], h]);
  map.setFilter("perim-front", ["==", ["get", "hour"], h]);
  map.setFilter("perim-rings", ["all", ["<=", ["get", "hour"], hour], ["==", ["%", ["get", "hour"], 6], 0]]);
  // Satellite detections from the last 6 hours, fading with age.
  map.setFilter("detections", ["all", ["<=", ["get", "hour"], hour], [">", ["get", "hour"], hour - 6]]);
  map.setPaintProperty("detections", "circle-opacity", ["interpolate", ["linear"], ["get", "hour"], hour - 6, 0.25, hour, 1]);
  map.setFilter("structures", ["all", ["has", "sim_arrival_hr"], ["!=", ["get", "sim_arrival_hr"], null],
    ["<=", ["to-number", ["get", "sim_arrival_hr"]], hour]]);
}

function setVisibility(map, incident, layers) {
  const live = incident === "live";
  for (const [key, ids] of Object.entries(LAYER_GROUPS)) {
    const scoped = LIVE_KEYS.has(key) ? live : INCIDENT_KEYS.has(key) ? !live : true;
    for (const id of ids) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", layers[key] && scoped ? "visible" : "none");
  }
  for (const id of ["ignition", "ignition-label"]) map.setLayoutProperty(id, "visibility", live ? "none" : "visible");
}

function flyToIncident(map, incident, data, camera) {
  const cam = CAMERAS[camera] ?? CAMERAS["3d"];
  if (incident === "live" || !data) {
    map.flyTo({ ...CAMERAS.overview, duration: 2500 });
    return;
  }
  const { lon, lat } = data.info.ignition;
  map.flyTo({
    center: [lon, lat - 0.01], zoom: 11.3 + (cam.zoomDelta || 0), pitch: cam.pitch,
    bearing: cam.bearing ?? (incident === "eaton_2025" ? 160 : 200), duration: 2500,
  });
}

export default function MapView({ incident, data, hour, layers, basemap, light, camera, onReady }) {
  const el = useRef(null);
  const mapRef = useRef(null);
  const staticRef = useRef(null);
  const ready = useRef(false);
  // Latest props, so a style reload can restore everything.
  const state = useRef({});
  state.current = { incident, data, hour, layers, basemap, light, camera };

  const restore = (map) => {
    const s = state.current;
    if (BASEMAPS[s.basemap].standard) map.setConfigProperty("basemap", "lightPreset", s.light);
    install(map, staticRef.current, !!BASEMAPS[s.basemap].standard);
    setIncidentData(map, s.incident, s.data);
    setTime(map, s.hour);
    setVisibility(map, s.incident, s.layers);
  };

  useEffect(() => {
    if (!mapboxgl.accessToken) return;
    const map = new mapboxgl.Map({
      container: el.current,
      style: BASEMAPS[basemap].style,
      config: BASEMAPS[basemap].standard ? { basemap: { lightPreset: light } } : undefined,
      ...CAMERAS.overview,
      maxPitch: 85,
    });
    mapRef.current = map;
    map.addControl(new mapboxgl.NavigationControl({ visualizePitch: true }), "top-left");
    map.addControl(new mapboxgl.ScaleControl({ unit: "imperial" }), "bottom-left");

    let first = true;
    map.on("style.load", async () => {
      if (!staticRef.current) staticRef.current = await loadStatic();
      restore(map);
      ready.current = true;
      if (first) {
        first = false;
        onReady?.();
        flyToIncident(map, state.current.incident, state.current.data, state.current.camera);
      }
    });
    return () => map.remove();
  }, []);

  // Basemap swap: setStyle wipes custom layers; style.load reinstalls them.
  const lastBasemap = useRef(basemap);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || lastBasemap.current === basemap) return;
    lastBasemap.current = basemap;
    ready.current = false;
    map.setStyle(BASEMAPS[basemap].style);
  }, [basemap]);

  useEffect(() => {
    const map = mapRef.current;
    if (ready.current && BASEMAPS[basemap].standard) map.setConfigProperty("basemap", "lightPreset", light);
  }, [light]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready.current) return;
    setIncidentData(map, incident, data);
    setVisibility(map, incident, layers);
    flyToIncident(map, incident, data, camera);
  }, [incident, data]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready.current) return;
    if (camera === "overview") {
      map.flyTo({ ...CAMERAS.overview, duration: 1800 });
      return;
    }
    const cam = CAMERAS[camera];
    map.easeTo({
      pitch: cam.pitch,
      bearing: cam.bearing ?? map.getBearing(),
      zoom: camera === "ground" ? Math.max(map.getZoom(), 12) : map.getZoom(),
      duration: 1500,
    });
  }, [camera]);

  useEffect(() => {
    if (ready.current) setTime(mapRef.current, hour);
  }, [hour, data]);

  useEffect(() => {
    if (ready.current) setVisibility(mapRef.current, incident, layers);
  }, [layers, incident]);

  if (!mapboxgl.accessToken) {
    return (
      <div className="map notoken">
        <div>
          <b>Mapbox token missing.</b>
          <p>Add <code>VITE_MAPBOX_TOKEN=pk...</code> to <code>web/.env.local</code> and restart <code>npm run dev</code>.</p>
        </div>
      </div>
    );
  }
  return <div ref={el} className="map" />;
}
