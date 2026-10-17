// Buildings around a replayed fire, burning on the fire clock.
//
// Footprints are OpenStreetMap as of the day before ignition (baked by
// modeling/wildfire/buildings.py) with the modeled arrival hour and the CAL
// FIRE damage class. They stand on the same DEM the map renders, and a shader
// lights them by the sun, glows them as the front approaches, sets them alight
// when it arrives and chars them after. Buildings DINS recorded as destroyed
// (or majorly damaged) collapse into rubble; the rest only scorch, so the
// replay never claims a loss the records don't show.
import mapboxgl from "mapbox-gl";
import * as THREE from "three";

const TEX_W = 2048;

/** Per damage class (0 no record, 1 none, 2 affected, 3 minor, 4 major, 5 destroyed). */
export const BURN_HOURS = [0.7, 0.3, 0.5, 0.9, 2.2, 3.2];

export async function loadBuildings(base) {
  const [meta, buf] = await Promise.all([
    fetch(`${base}/buildings.json`).then((r) => (r.ok ? r.json() : null)),
    fetch(`${base}/buildings.bin`).then((r) => (r.ok ? r.arrayBuffer() : null)),
  ]).catch(() => [null, null]);
  if (!meta || !buf) return null;
  const N = meta.count, V = meta.vertices;
  let o = 0;
  const take = (Type, n) => {
    const a = new Type(buf, o, n);
    o += n * Type.BYTES_PER_ELEMENT;
    return a;
  };
  const arrival = take(Float32Array, N);
  const height = take(Float32Array, N);
  const starts = take(Uint32Array, N + 1);
  const verts = take(Float32Array, V * 2);
  const damage = new Uint8Array(buf, o, N);

  // Centroid + radius per building, and the order they catch, for spawning flames.
  const cx = new Float32Array(N), cy = new Float32Array(N), rad = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    let sx = 0, sy = 0;
    const a = starts[i], b = starts[i + 1];
    for (let k = a; k < b; k++) { sx += verts[k * 2]; sy += verts[k * 2 + 1]; }
    cx[i] = sx / (b - a);
    cy[i] = sy / (b - a);
    let r = 0;
    for (let k = a; k < b; k++) r = Math.max(r, Math.hypot(verts[k * 2] - cx[i], verts[k * 2 + 1] - cy[i]));
    rad[i] = r;
  }
  const reached = [];
  for (let i = 0; i < N; i++) if (arrival[i] >= 0) reached.push(i);
  const order = Uint32Array.from(reached).sort((a, b) => arrival[a] - arrival[b]);
  const times = Float32Array.from(order, (i) => arrival[i]);
  return { meta, N, arrival, height, starts, verts, damage, cx, cy, rad, order, times, base: null };
}

/** Building origin (shifted) in Mercator, and Mercator units per local metre. */
export function buildingFrame(b, shift = [0, 0]) {
  const o = mapboxgl.MercatorCoordinate.fromLngLat([b.meta.origin[0] + shift[0], b.meta.origin[1] + shift[1]]);
  const lat = b.meta.origin[1];
  return { ox: o.x, oy: o.y, s: mapboxgl.MercatorCoordinate.fromLngLat([0, lat]).meterInMercatorCoordinateUnits() };
}

/**
 * Ground under each building: `base` is the mean over the footprint (where the
 * house stands), `low` / `high` its lowest and highest corners (how far the
 * walls reach down a slope, and how high the roof must clear it).
 */
export function groundBuildings(b, dem, frame) {
  const base = new Float32Array(b.N), low = new Float32Array(b.N), high = new Float32Array(b.N);
  const { ox, oy, s } = frame;
  for (let i = 0; i < b.N; i++) {
    let lo = dem.at(ox + b.cx[i] * s, oy + b.cy[i] * s), hi = lo, sum = lo;
    for (let k = b.starts[i]; k < b.starts[i + 1]; k++) {
      const z = dem.at(ox + b.verts[k * 2] * s, oy + b.verts[k * 2 + 1] * s);
      lo = Math.min(lo, z);
      hi = Math.max(hi, z);
      sum += z;
    }
    base[i] = sum / (b.starts[i + 1] - b.starts[i] + 1);
    low[i] = lo;
    high[i] = hi;
  }
  Object.assign(b, { base, low, high });
}

export function buildingGeometry(b) {
  const { N, starts, verts } = b;
  let nv = 0, ni = 0;
  for (let i = 0; i < N; i++) {
    const m = starts[i + 1] - starts[i];
    nv += m * 4 + m;
    ni += m * 6 + (m - 2) * 3;
  }
  const pos = new Float32Array(nv * 3);
  const misc = new Float32Array(nv * 4); // wall normal (x east, y south) | 0,0 for roofs; u along wall; building id
  const idx = new Uint32Array(ni);
  let v = 0, t = 0;
  const contour = [];
  for (let i = 0; i < N; i++) {
    const a = starts[i], m = starts[i + 1] - a;
    let area = 0;
    for (let k = 0; k < m; k++) {
      const p = (a + k) * 2, q = (a + ((k + 1) % m)) * 2;
      area += verts[p] * verts[q + 1] - verts[q] * verts[p + 1];
    }
    const sgn = area >= 0 ? 1 : -1;
    let u = 0;
    for (let k = 0; k < m; k++) {
      const p = (a + k) * 2, q = (a + ((k + 1) % m)) * 2;
      const x0 = verts[p], y0 = verts[p + 1], x1 = verts[q], y1 = verts[q + 1];
      const len = Math.hypot(x1 - x0, y1 - y0) || 1;
      const nx = (sgn * (y1 - y0)) / len, ny = (-sgn * (x1 - x0)) / len;
      for (let c = 0; c < 4; c++) {
        const far = c === 1 || c === 2;
        pos[v * 3] = far ? x1 : x0;
        pos[v * 3 + 1] = far ? y1 : y0;
        pos[v * 3 + 2] = c >= 2 ? 1 : 0;
        misc[v * 4] = nx;
        misc[v * 4 + 1] = ny;
        misc[v * 4 + 2] = far ? u + len : u;
        misc[v * 4 + 3] = i;
        v++;
      }
      const w = v - 4;
      idx[t++] = w; idx[t++] = w + 1; idx[t++] = w + 2;
      idx[t++] = w; idx[t++] = w + 2; idx[t++] = w + 3;
      u += len;
    }
    const r0 = v;
    for (let k = 0; k < m; k++) {
      pos[v * 3] = verts[(a + k) * 2];
      pos[v * 3 + 1] = verts[(a + k) * 2 + 1];
      pos[v * 3 + 2] = 1;
      misc[v * 4 + 3] = i; // roof: zero normal
      v++;
    }
    // Most footprints are rectangles: fan them; triangulate the rest properly.
    const tris = m <= 4 ? (m === 3 ? [[0, 1, 2]] : [[0, 1, 2], [0, 2, 3]]) : null;
    if (!tris) {
      contour.length = 0;
      for (let k = 0; k < m; k++) contour.push(new THREE.Vector2(verts[(a + k) * 2], verts[(a + k) * 2 + 1]));
    }
    for (const tri of tris ?? THREE.ShapeUtils.triangulateShape(contour, [])) {
      idx[t++] = r0 + tri[0]; idx[t++] = r0 + tri[1]; idx[t++] = r0 + tri[2];
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("aMisc", new THREE.BufferAttribute(misc, 4));
  geo.setIndex(new THREE.BufferAttribute(idx.subarray(0, t), 1));
  return geo;
}

/** Per-building data texture, two texels each: (arrival, height, ground, damage + seed), (lowest ground). */
export function buildingTexture(b) {
  const H = Math.ceil((b.N * 2) / TEX_W);
  const data = new Float32Array(TEX_W * H * 4);
  for (let i = 0; i < b.N; i++) {
    const seed = ((Math.sin(i * 12.9898) * 43758.5453) % 1 + 1) % 1;
    data.set([b.arrival[i], b.height[i], b.base[i], b.damage[i] + seed * 0.99, b.low[i], b.high[i]], i * 8);
  }
  const tex = new THREE.DataTexture(data, TEX_W, H, THREE.RGBAFormat, THREE.FloatType);
  tex.minFilter = tex.magFilter = THREE.NearestFilter;
  tex.needsUpdate = true;
  return tex;
}

const NOISE = /* glsl */ `
  float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
  float vnoise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
  }
  float fbm3(vec2 p) {
    float s = 0.0, a = 0.5;
    for (int i = 0; i < 3; i++) { s += a * vnoise(p); p = mat2(1.6, 1.2, -1.2, 1.6) * p; a *= 0.5; }
    return s;
  }
  float burnHours(float d) {
    return d < 0.5 ? ${BURN_HOURS[0].toFixed(2)} : d < 1.5 ? ${BURN_HOURS[1].toFixed(2)} : d < 2.5 ? ${BURN_HOURS[2].toFixed(2)}
         : d < 3.5 ? ${BURN_HOURS[3].toFixed(2)} : d < 4.5 ? ${BURN_HOURS[4].toFixed(2)} : ${BURN_HOURS[5].toFixed(2)};
  }
`;

const VERT = /* glsl */ `
  precision highp float;
  precision highp sampler2D;
  uniform mat4 uMatrix;
  uniform sampler2D uData;
  uniform float uT;
  uniform float uExag;
  in vec3 position;
  in vec4 aMisc;
  out vec3 vNormal;
  out vec2 vWall;      // metres along the wall, metres above the ground
  out vec2 vXY;
  out float vHeight;   // standing height now
  out float vDt;
  out float vDamage;
  out float vSeed;
  out float vRoof;
  ${NOISE}

  void main() {
    int id = int(aMisc.w + 0.5);
    int px = id * 2;
    vec4 b = texelFetch(uData, ivec2(px % ${TEX_W}, px / ${TEX_W}), 0);
    vec2 lohi = texelFetch(uData, ivec2((px + 1) % ${TEX_W}, (px + 1) / ${TEX_W}), 0).rg;
    float dmg = floor(b.w);
    vSeed = fract(b.w);
    vDamage = dmg;
    float dt = b.x < 0.0 ? -1e4 : uT - b.x;
    vDt = dt;
    // Destroyed homes fall to rubble over their burn; major damage loses its roof line.
    float collapse = dmg > 4.5 ? 0.86 : dmg > 3.5 ? 0.42 : 0.0;
    float burn = burnHours(dmg);
    float k = smoothstep(burn * 0.2, burn * 0.9, dt) * collapse;
    float jag = hash(position.xy * 0.37 + vSeed * 17.0);
    // On a slope (exaggerated with the terrain) the roof still clears the uphill corner.
    float standing = max(b.y, (lohi.y - b.z) * uExag + b.y * 0.6);
    float h = standing * (1.0 - k * (0.8 + 0.2 * jag));
    vHeight = h;
    // Walls run down past the lowest corner so neither the slope nor DEM mismatch shows a gap.
    float skirt = (b.z - lohi.x) * uExag + 3.0 * uExag;
    float above = position.z > 0.5 ? h : -skirt;
    vWall = vec2(aMisc.z, above);
    vXY = position.xy;
    vRoof = (aMisc.x == 0.0 && aMisc.y == 0.0) ? 1.0 : 0.0;
    vNormal = vRoof > 0.5 ? vec3(0.0, 0.0, 1.0) : vec3(aMisc.xy, 0.0);
    gl_Position = uMatrix * vec4(position.xy, b.z * uExag + above, 1.0);
  }
`;

const FRAG = /* glsl */ `
  precision highp float;
  uniform float uClock;
  uniform float uDark;
  uniform vec3 uSunDir;   // east, south, up
  uniform vec3 uSunColor;
  uniform float uAmbient;
  in vec3 vNormal;
  in vec2 vWall;
  in vec2 vXY;
  in float vHeight;
  in float vDt;
  in float vDamage;
  in float vSeed;
  in float vRoof;
  out vec4 outColor;
  ${NOISE}

  vec3 flameRamp(float h) {
    vec3 c = mix(vec3(0.45, 0.04, 0.0), vec3(0.95, 0.22, 0.03), smoothstep(0.0, 0.35, h));
    c = mix(c, vec3(1.0, 0.55, 0.1), smoothstep(0.3, 0.65, h));
    return mix(c, vec3(1.0, 0.9, 0.6), smoothstep(0.7, 1.0, h));
  }

  void main() {
    vec3 n = normalize(vNormal);
    float lam = max(dot(n, normalize(uSunDir)), 0.0);
    // Stucco walls, tile / composition roofs, varied per house.
    vec3 wall = mix(vec3(0.86, 0.81, 0.72), vec3(0.93, 0.91, 0.87), step(0.45, vSeed));
    wall = mix(wall, vec3(0.78, 0.7, 0.6), step(0.82, vSeed));
    vec3 roof = vSeed < 0.38 ? vec3(0.62, 0.3, 0.2) : vSeed < 0.72 ? vec3(0.4, 0.4, 0.42) : vec3(0.26, 0.26, 0.28);
    vec3 albedo = vRoof > 0.5 ? roof : wall;
    // Ambient occlusion where walls meet the ground, and a darker band on shaded sides.
    float ao = vRoof > 0.5 ? 1.0 : mix(0.5, 1.0, smoothstep(-0.5, 2.5, vWall.y));
    vec3 col = albedo * (uAmbient * mix(vec3(0.75, 0.8, 0.95), vec3(1.0), 0.5) + lam * uSunColor) * ao;

    float burn = burnHours(vDamage);
    float dt = vDt;
    float yFrac = clamp(vWall.y / max(vHeight, 1.0), 0.0, 1.0);

    // Windows: lit after dark until the fire reaches the house.
    float win = 0.0;
    if (vRoof < 0.5 && vHeight > 2.6) {
      vec2 cell = vec2(vWall.x / 3.4, (vWall.y - 0.4) / 2.9);
      vec2 f = fract(cell);
      win = step(0.3, f.x) * step(f.x, 0.7) * step(0.3, f.y) * step(f.y, 0.75) * step(0.0, vWall.y - 0.6) * step(vWall.y, vHeight - 0.6);
      float lit = step(0.45, hash(floor(cell) + vSeed * 31.0)) * uDark * (dt < 0.0 ? 1.0 : 0.0);
      col = mix(col, col * 0.55, win * 0.6);
      col += win * lit * vec3(1.0, 0.72, 0.38) * 0.9;
    }

    vec3 glow = vec3(0.0);
    // The approaching front lights the house orange.
    if (dt > -1.5 && dt < 0.0) glow += vec3(1.0, 0.38, 0.08) * exp(dt / 0.3) * (0.25 + 0.35 * (1.0 - yFrac)) * (0.6 + 0.4 * uDark);

    if (dt >= 0.0) {
      // Char: total on destroyed homes, a soot band on scorched ones.
      float charAmt = vDamage > 4.5 ? 1.0 : vDamage > 3.5 ? 0.9 : vDamage > 2.5 ? 0.55 : vDamage > 1.5 ? 0.35 : vDamage > 0.5 ? 0.15 : 0.3;
      float reach = vDamage > 3.5 ? 1.2 : mix(0.25, 0.8, charAmt);
      float soot = smoothstep(0.05, burn * 0.8, dt) * charAmt * (vRoof > 0.5 ? step(3.5, vDamage) : 1.0 - smoothstep(reach - 0.2, reach + 0.1, yFrac));
      float ash = fbm3(vXY * 0.6 + vWall * 0.4);
      vec3 charcoal = mix(vec3(0.05, 0.045, 0.04), vec3(0.18, 0.16, 0.14), ash) * (0.5 + 0.7 * lam);
      col = mix(col, charcoal, clamp(soot, 0.0, 1.0));

      // Flames climbing the walls and through the roof while it burns.
      float env = smoothstep(0.0, 0.06, dt) * (1.0 - smoothstep(burn * 0.45, burn, dt));
      float cover = vDamage > 3.5 ? 1.15 : vDamage > 0.5 ? 0.25 + 0.3 * charAmt : 0.55;
      vec2 q = vec2(vWall.x * 0.35 + vXY.x * 0.12, vWall.y * 0.28 - uClock * 2.6) + vSeed * 10.0;
      float f = fbm3(q) + 0.35 * vnoise(q * 3.0 + uClock);
      float top = cover * (0.35 + 0.9 * f);
      float tongue = 1.0 - smoothstep(top - 0.35, top, yFrac);
      if (vRoof > 0.5) tongue = vDamage > 3.5 ? smoothstep(0.35, 0.75, f) : 0.0;
      // Broken tongues with charred wall showing between them, never a white sheet.
      float heat = env * tongue * smoothstep(0.25, 0.75, f) * (0.55 + 0.45 * vnoise(q * 2.0 - uClock * 1.5));
      // Interior fire behind the windows.
      heat = max(heat, env * win * step(3.5, vDamage) * (0.6 + 0.25 * sin(uClock * 9.0 + vSeed * 40.0)));
      glow += flameRamp(clamp(heat * 0.85, 0.0, 0.85)) * heat * 1.5;

      // Smouldering rubble for hours afterwards.
      if (vDamage > 3.5) {
        float speck = smoothstep(0.62, 0.92, vnoise(vXY * 1.7 + vWall * 0.9) * 0.7 + vnoise(vXY * 5.0 + uClock * 0.7) * 0.4);
        glow += vec3(1.0, 0.3, 0.05) * speck * exp(-max(dt - burn * 0.6, 0.0) / 6.0) * (1.0 - smoothstep(0.0, 0.4, yFrac)) * 1.4;
      }
    }
    outColor = vec4(col + glow, 1.0);
  }
`;

export function buildingMaterial() {
  return new THREE.RawShaderMaterial({
    glslVersion: THREE.GLSL3,
    vertexShader: VERT,
    fragmentShader: FRAG,
    uniforms: {
      uMatrix: { value: new THREE.Matrix4() }, uData: { value: null }, uT: { value: 0 }, uExag: { value: 1 },
      uClock: { value: 0 }, uDark: { value: 0 }, uSunDir: { value: new THREE.Vector3(0, 0, 1) },
      uSunColor: { value: new THREE.Vector3(1, 1, 1) }, uAmbient: { value: 0.4 },
    },
    side: THREE.DoubleSide,
    depthTest: true,
    depthWrite: true,
    transparent: false,
  });
}
