// Terrain shared by the map, the fire shader and the buildings.
//
// The map draws Mapbox's terrain-dem tiles exaggerated by EXAGGERATION. For
// the fire replay we also need the elevations ourselves (to light the burn
// scar by slope, to make flames run harder upslope and to stand buildings on
// the ground), so loadDem fetches the same tileset over the fire's extent
// and decodes it into one Float32 grid in Web Mercator.
import mapboxgl from "mapbox-gl";

// Mountains stand ~2.7x tall at fire scale; among houses the slope eases off so
// streets and buildings don't sit on cliffs.
const EXAG_STOPS = [[10, 3.0], [12, 2.7], [14, 2.0], [16, 1.5], [17.5, 1.35]];
export const EXAGGERATION = ["interpolate", ["linear"], ["zoom"], ...EXAG_STOPS.flat()];

/** The exaggeration the map is drawing at this zoom (matches EXAGGERATION). */
export function exaggerationAt(zoom) {
  if (zoom <= EXAG_STOPS[0][0]) return EXAG_STOPS[0][1];
  for (let i = 1; i < EXAG_STOPS.length; i++) {
    const [z1, e1] = EXAG_STOPS[i];
    if (zoom <= z1) {
      const [z0, e0] = EXAG_STOPS[i - 1];
      return e0 + ((e1 - e0) * (zoom - z0)) / (z1 - z0);
    }
  }
  return EXAG_STOPS[EXAG_STOPS.length - 1][1];
}
export const DEM_SOURCE = "mapbox://mapbox.mapbox-terrain-dem-v1";
const DEM_TILES = "https://api.mapbox.com/v4/mapbox.mapbox-terrain-dem-v1";
const MAX_TILES = 64;

const tile = (z, x, y) =>
  fetch(`${DEM_TILES}/${z}/${x}/${y}@2x.pngraw?access_token=${mapboxgl.accessToken}`)
    .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(`dem ${r.status}`))))
    // Raw elevations: no colour management or premultiplication may touch the bytes.
    .then((b) => createImageBitmap(b, { colorSpaceConversion: "none", premultiplyAlpha: "none" }));

/**
 * Elevation grid (metres, not exaggerated) covering a Mercator box.
 * bounds = [mx0, my0, mx1, my1] in Mercator units (y down).
 */
export async function loadDem([mx0, my0, mx1, my1], maxZoom = 13) {
  let z = maxZoom;
  const span = (zz) => {
    const n = 2 ** zz;
    return [Math.floor(mx0 * n), Math.floor(my0 * n), Math.floor(mx1 * n), Math.floor(my1 * n)];
  };
  let [tx0, ty0, tx1, ty1] = span(z);
  while ((tx1 - tx0 + 1) * (ty1 - ty0 + 1) > MAX_TILES && z > 9) [tx0, ty0, tx1, ty1] = span(--z);
  const T = 512;
  const nx = tx1 - tx0 + 1, ny = ty1 - ty0 + 1;
  const W = nx * T, H = ny * T;
  const canvas = new OffscreenCanvas(T, T);
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const data = new Float32Array(W * H);
  const jobs = [];
  for (let ty = ty0; ty <= ty1; ty++) {
    for (let tx = tx0; tx <= tx1; tx++) jobs.push(tile(z, tx, ty).then((img) => [tx, ty, img]).catch(() => null));
  }
  for (const r of await Promise.all(jobs)) {
    if (!r) continue;
    const [tx, ty, img] = r;
    ctx.clearRect(0, 0, T, T);
    ctx.drawImage(img, 0, 0, T, T);
    img.close?.();
    const px = ctx.getImageData(0, 0, T, T).data;
    const ox = (tx - tx0) * T, oy = (ty - ty0) * T;
    for (let y = 0; y < T; y++) {
      let o = (oy + y) * W + ox, i = y * T * 4;
      for (let x = 0; x < T; x++, i += 4) data[o++] = -10000 + (px[i] * 65536 + px[i + 1] * 256 + px[i + 2]) * 0.1;
    }
  }
  const n = 2 ** z;
  const bounds = [tx0 / n, ty0 / n, (tx1 + 1) / n, (ty1 + 1) / n];
  // Metres per DEM pixel at the box's centre latitude.
  const lat = new mapboxgl.MercatorCoordinate(0, (my0 + my1) / 2).toLngLat().lat;
  const mpp = (bounds[2] - bounds[0]) / W / mapboxgl.MercatorCoordinate.fromLngLat([0, lat]).meterInMercatorCoordinateUnits();
  return new Dem(data, W, H, bounds, mpp, z);
}

export class Dem {
  constructor(data, W, H, bounds, mpp, zoom) {
    Object.assign(this, { data, W, H, bounds, mpp, zoom });
  }

  /** Mercator -> fractional DEM pixel (pixel centres at +0.5). */
  pixel(mx, my) {
    const [x0, y0, x1, y1] = this.bounds;
    return [((mx - x0) / (x1 - x0)) * this.W, ((my - y0) / (y1 - y0)) * this.H];
  }

  /** Bilinear elevation (metres) at a Mercator point. */
  at(mx, my) {
    const [px, py] = this.pixel(mx, my);
    const x = Math.min(Math.max(px - 0.5, 0), this.W - 1.001), y = Math.min(Math.max(py - 0.5, 0), this.H - 1.001);
    const i = Math.floor(x), j = Math.floor(y), fx = x - i, fy = y - j;
    const d = this.data, W = this.W, o = j * W + i;
    return (d[o] * (1 - fx) + d[o + 1] * fx) * (1 - fy) + (d[o + W] * (1 - fx) + d[o + W + 1] * fx) * fy;
  }

  /** Elevation gradient (dz/dx east, dz/dy south), metres per metre. */
  gradient(mx, my) {
    const [x0, , x1] = this.bounds;
    const step = ((x1 - x0) / this.W) * 1.5;
    const k = 1 / (2 * this.mpp * 1.5);
    return [(this.at(mx + step, my) - this.at(mx - step, my)) * k, (this.at(mx, my + step) - this.at(mx, my - step)) * k];
  }
}

// Light direction per Mapbox Standard light preset, as (east, south, up). Low sun at
// dawn and dusk rakes across the slopes, which is what makes relief read.
export const SUN = {
  day: { dir: [-0.45, 0.55, 0.7], color: [1.0, 0.97, 0.92], ambient: 0.5 },
  dawn: { dir: [0.85, 0.2, 0.32], color: [1.0, 0.78, 0.6], ambient: 0.36 },
  dusk: { dir: [-0.88, 0.18, 0.28], color: [1.0, 0.66, 0.45], ambient: 0.3 },
  night: { dir: [-0.3, 0.6, 0.62], color: [0.55, 0.62, 0.85], ambient: 0.16 },
};
