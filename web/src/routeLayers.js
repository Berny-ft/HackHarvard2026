// Safe-route layers shared by the fire map (MapView) and the surge map (SurgeMapView): roads closed at
// departure (by restriction), closed stretches of the plain fastest route (violet), the route with a glow,
// a fading dashed tail where an escape leaves the hazard's mapped area, and the A / B / S pins.
import mapboxgl from "mapbox-gl";
import { BLOCKING_COLOR, CLOSED_COLOR } from "./RoutePanel.jsx";

const EMPTY = { type: "FeatureCollection", features: [] };
const SOURCES = ["route-closed", "route-blocking", "route-path", "route-beyond", "route-pins"];

export function installRouteLayers(map, add, color = "#bade61") {
  for (const id of SOURCES) map.addSource(id, { type: "geojson", data: EMPTY });
  add({ id: "route-closed", type: "line", source: "route-closed", layout: { "line-cap": "round" },
        paint: { "line-color": ["match", ["get", "restriction"],
                   "perimeter", CLOSED_COLOR.perimeter, "hotspot", CLOSED_COLOR.hotspot, "closure", CLOSED_COLOR.closure,
                   "flood", CLOSED_COLOR.flood, "bridge", CLOSED_COLOR.bridge, "tunnel", CLOSED_COLOR.tunnel,
                   CLOSED_COLOR.arrival],
                 "line-width": ["interpolate", ["linear"], ["zoom"], 9, 1.2, 14, 3.5], "line-opacity": 0.8 } }, "top");
  add({ id: "route-blocking", type: "line", source: "route-blocking", layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": BLOCKING_COLOR, "line-width": ["interpolate", ["linear"], ["zoom"], 9, 3, 14, 7] } }, "top");
  add({ id: "route-glow", type: "line", source: "route-path", layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": color, "line-width": 12, "line-blur": 8, "line-opacity": 0.45 } }, "top");
  add({ id: "route-path", type: "line", source: "route-path", layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": color, "line-width": 4 } }, "top");
  // Past the edge of the mapped area the route keeps going, fading out as a dashed line.
  add({ id: "route-beyond", type: "line", source: "route-beyond", layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": 4, "line-dasharray": [1.2, 1.6], "line-opacity": 0.8 } }, "top");
  add({ id: "route-pins", type: "circle", source: "route-pins",
        paint: { "circle-radius": 9, "circle-color": ["match", ["get", "label"], "A", color, "#ffffff"],
                 "circle-stroke-color": "#0c1714", "circle-stroke-width": 2 } }, "top");
  add({ id: "route-pin-labels", type: "symbol", source: "route-pins",
        layout: { "text-field": ["get", "label"], "text-size": 11, "text-allow-overlap": true },
        paint: { "text-color": "#0c1714" } }, "top");
  add({ id: "route-edge-label", type: "symbol", source: "route-beyond",
        layout: { "symbol-placement": "line-center", "text-field": "Leaves the flood-mapped area", "text-size": 12,
                  "text-offset": [0, -1.1], "text-allow-overlap": true },
        paint: { "text-color": "#ffffff", "text-halo-color": "rgba(0,0,0,0.8)", "text-halo-width": 1.4 } }, "top");
}

export function setRoute(map, route) {
  if (!map.getSource("route-path")) return;
  const escape = route?.kind === "escape";
  const r = route?.result && (route.result.kind === "escape") === escape ? route.result : null;
  map.getSource("route-path").setData(r?.status === "ok" && r.path ? r.path : EMPTY);
  map.getSource("route-beyond").setData(r?.status === "ok" && r.beyond_mapped_area ? r.beyond_mapped_area : EMPTY);
  map.getSource("route-closed").setData(r?.closed_at_departure ?? EMPTY);
  map.getSource("route-blocking").setData(r?.blocking ?? EMPTY);
  const safe = escape && r?.status === "ok" ? r.safe_point : null;
  const pins = [["A", route?.from], ["B", escape ? null : route?.to], ["S", safe]]
    .filter(([, p]) => p).map(([label, [lat, lon]]) => ({
      type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties: { label },
    }));
  map.getSource("route-pins").setData({ type: "FeatureCollection", features: pins });
  map.getCanvas().style.cursor = route?.picking ? "crosshair" : "";
}

// Fit the camera to a route once per new A/B pair (not on every timeline hour).
export function frameRoute(map, route, framed) {
  const coords = route?.result?.path?.geometry.coordinates;
  const key = route?.from && (route?.to || route?.kind === "escape") ? `${route.kind}|${route.from}|${route.to}` : null;
  if (!coords?.length || key === framed.current) return;
  framed.current = key;
  const b = coords.reduce((bb, c) => bb.extend(c), new mapboxgl.LngLatBounds(coords[0], coords[0]));
  map.fitBounds(b, { padding: 80, pitch: 45, duration: 1800 });
}
