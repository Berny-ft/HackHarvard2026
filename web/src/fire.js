// Modeled fire spread, rendered from the simulation's per-cell arrival time.
//
// Ground: a fragment shader turns arrival time into a continuous fire front
// (flaming zone, smouldering embers, burn scar, 6-hour isochrones) on a
// WebGL canvas that Mapbox drapes over the terrain as a canvas source.
// Air: a three.js custom layer sharing Mapbox's GL context draws flames,
// lofted embers and a smoke plume that rise from the live front and drift
// downwind. Positions come from the data; the turbulence is stylised.
import mapboxgl from "mapbox-gl";
import * as THREE from "three";

const GROUND = "fire-ground";
const AIR = "fire-air";
const MAX_CANVAS = 1400;
const GROUND_FPS = 24; // drape uploads re-render terrain tiles, so keep them modest

// --- data ------------------------------------------------------------------

/** Decode arrival.png (R,G = 16-bit arrival hour, B = sqrt spread rate) into typed arrays. */
export async function loadArrival(base) {
  const meta = await fetch(`${base}/arrival.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!meta) return null;
  const img = new Image();
  img.src = `${base}/arrival.png`;
  await img.decode();
  const { width: W, height: H } = img;
  const c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0);
  const px = ctx.getImageData(0, 0, W, H).data;
  const hours = meta.hours;
  const never = hours + 24; // soft ceiling so the front interpolates cleanly into unburned cells
  const field = new Float32Array(W * H * 2); // [arrival hour, spread 0-1]
  const burned = [];
  let x0 = W, y0 = H, x1 = -1, y1 = -1;
  for (let i = 0; i < W * H; i++) {
    const q = px[i * 4] * 256 + px[i * 4 + 1];
    const a = q === 65535 ? never : (q / 65534) * hours;
    field[i * 2] = a;
    field[i * 2 + 1] = px[i * 4 + 2] / 255;
    if (q !== 65535) {
      burned.push(i);
      const x = i % W, y = (i / W) | 0;
      if (x < x0) x0 = x;
      if (x > x1) x1 = x;
      if (y < y0) y0 = y;
      if (y > y1) y1 = y;
    }
  }
  // Burned cells sorted by arrival, so the live front is a contiguous slice.
  const order = Uint32Array.from(burned).sort((a, b) => field[a * 2] - field[b * 2]);
  const times = Float32Array.from(order, (i) => field[i * 2]);
  const m = 16;
  const crop = x1 < 0 ? [0, 0, W, H] : [Math.max(0, x0 - m), Math.max(0, y0 - m), Math.min(W, x1 + m + 1), Math.min(H, y1 + m + 1)];
  return { W, H, hours, field, order, times, crop, corners: meta.coordinates };
}

// Texel (x right, y down) -> mercator, linear between the image corners (TL, TR, BR, BL).
function texelMercator(arr, shift, x, y) {
  const [tl, , br] = arr.corners;
  const a = mapboxgl.MercatorCoordinate.fromLngLat([tl[0] + shift[0], tl[1] + shift[1]]);
  const b = mapboxgl.MercatorCoordinate.fromLngLat([br[0] + shift[0], br[1] + shift[1]]);
  return [a.x + (b.x - a.x) * (x / arr.W), a.y + (b.y - a.y) * (y / arr.H)];
}

function lowerBound(arr, v) {
  let lo = 0, hi = arr.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] < v) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

// --- shaders ---------------------------------------------------------------

const NOISE = /* glsl */ `
  float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
  float vnoise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
  }
  float fbmN(vec2 p, int oct) {
    float s = 0.0, a = 0.5;
    for (int i = 0; i < oct; i++) { s += a * vnoise(p); p = mat2(1.6, 1.2, -1.2, 1.6) * p; a *= 0.5; }
    return s;
  }
  float fbm(vec2 p) { return fbmN(p, 5); }
`;

const GROUND_FRAG = /* glsl */ `
  precision highp float;
  uniform sampler2D uField;
  uniform vec2 uSize;     // texture size in texels
  uniform vec4 uCrop;     // x0, y0, x1, y1 in texels (y down)
  uniform float uT;       // fire clock, hours since ignition
  uniform float uClock;   // wall clock, seconds (flicker)
  uniform float uHours;
  uniform float uMpt;     // metres per texel
  uniform vec2 uWind;     // downwind unit vector in texel space
  uniform float uWindMph;
  in vec2 vUv;
  out vec4 outColor;
  ${NOISE}

  // Bilinear over burnable cells only (x = arrival, y = spread, z = coverage), so
  // cells that never burn don't smear fake arrival times along the final edge.
  vec3 cell(ivec2 p, float w) {
    vec2 c = texelFetch(uField, clamp(p, ivec2(0), ivec2(uSize) - 1), 0).rg;
    float b = w * step(c.x, uHours + 0.5);
    return vec3(c * b, b);
  }
  vec3 field(vec2 t) {
    vec2 p = t - 0.5;
    ivec2 i = ivec2(floor(p));
    vec2 f = fract(p);
    vec3 s = cell(i, (1.0 - f.x) * (1.0 - f.y)) + cell(i + ivec2(1, 0), f.x * (1.0 - f.y))
           + cell(i + ivec2(0, 1), (1.0 - f.x) * f.y) + cell(i + ivec2(1, 1), f.x * f.y);
    return s.z > 1e-4 ? vec3(s.xy / s.z, s.z) : vec3(uHours + 24.0, 0.0, 0.0);
  }

  vec3 flameRamp(float h) {
    vec3 c = mix(vec3(0.35, 0.02, 0.0), vec3(0.85, 0.12, 0.02), smoothstep(0.0, 0.3, h));
    c = mix(c, vec3(1.0, 0.45, 0.05), smoothstep(0.25, 0.6, h));
    c = mix(c, vec3(1.0, 0.82, 0.3), smoothstep(0.55, 0.85, h));
    return mix(c, vec3(1.0, 0.97, 0.85), smoothstep(0.85, 1.0, h));
  }

  void main() {
    vec2 t = mix(uCrop.xy, uCrop.zw, vec2(vUv.x, 1.0 - vUv.y));
    vec2 m = t * uMpt; // metres, for noise scales
    // Ragged, slowly boiling edge instead of a clean isoline.
    vec2 warp = vec2(fbm(m / 140.0 + uClock * 0.07), fbm(m / 140.0 + 17.3 - uClock * 0.06)) - 0.5;
    vec2 tw = t + warp * (90.0 / uMpt);
    vec3 s = field(tw);
    float a = s.x, ros = s.y;
    float inside = smoothstep(0.3, 0.6, s.z);
    if (inside <= 0.0 || uT < a - 2.0) { outColor = vec4(0.0); return; }

    vec2 g2 = vec2(field(tw + vec2(1, 0)).x - field(tw - vec2(1, 0)).x, field(tw + vec2(0, 1)).x - field(tw - vec2(0, 1)).x) * 0.5;
    float g = max(length(g2), 1e-3);       // hours per texel
    float dt = uT - a;                     // hours since the front passed
    float dist = dt / g * uMpt;            // metres behind the front (negative = ahead)
    float depth = mix(110.0, 420.0, ros);   // flaming-zone depth, deeper on the head fire

    // Turbulence streams downwind and upward.
    vec2 flow = m / 70.0 - uWind * uClock * (0.6 + uWindMph * 0.05);
    float n = fbm(flow + vec2(0.0, uClock * 0.9));
    float n2 = vnoise(m / 22.0 + uClock * 2.3);

    vec3 col = vec3(0.0);
    float alpha = 0.0;

    // Burn scar: charcoal, with ash texture and faint 6-hour isochrones.
    if (dt > 0.0) {
      float ash = fbm(m / 55.0);
      vec3 scar = mix(vec3(0.05, 0.04, 0.035), vec3(0.16, 0.13, 0.11), ash);
      float scarA = mix(0.5, 0.78, smoothstep(0.0, 0.25, dist / 400.0));
      float iso = abs(fract(a / 6.0 + 0.5) - 0.5) * 6.0 / g; // texels to nearest isochrone
      float line = (1.0 - smoothstep(0.3, 0.9, iso)) * smoothstep(2.0, 5.0, dt) * step(1.0, a);
      scar = mix(scar, vec3(0.75, 0.5, 0.36), line * 0.22);
      col = scar; alpha = scarA;
      // Residual heat: recently burned ground stays a dull red for hours (reads at night too).
      float residual = 0.55 * exp(-dt / 7.0) * (0.6 + 0.4 * ash);
      col = mix(col, vec3(0.5, 0.1, 0.03), residual);
      alpha = max(alpha, 0.6 * residual + 0.4);

      // Smouldering: ember speckle that dies out over a few hours.
      float smoulder = exp(-dt / mix(1.2, 3.5, ros)) * smoothstep(0.3, 1.2, dist / depth);
      float speck = smoothstep(0.62, 0.9, vnoise(m / 18.0) * 0.6 + n2 * 0.55);
      float glow = smoulder * (0.35 * n + speck * 1.3);
      col = mix(col, flameRamp(clamp(glow * 0.75, 0.0, 0.8)), clamp(glow, 0.0, 1.0));
      alpha = max(alpha, clamp(glow * 1.2, 0.0, 0.95));
    }

    // Flaming front: white-hot at the leading edge, cooling to deep red behind.
    float f = dist / depth;
    if (f > -0.3 && f < 1.4) {
      float lead = smoothstep(-0.12, 0.03, f) * (1.0 - smoothstep(0.85, 1.4, f + (n - 0.5) * 0.6));
      float heat = exp(-max(f, 0.0) * 2.4) * (0.55 + 0.8 * n) * (0.75 + 0.35 * ros);
      heat *= lead;
      vec3 fc = flameRamp(clamp(heat, 0.0, 1.0));
      float fa = clamp(heat * 1.6, 0.0, 1.0);
      col = mix(col, fc, fa);
      alpha = max(alpha, fa);
    }

    // Radiant glow just ahead of the front.
    if (dist < 0.0 && dist > -480.0) {
      float k = 1.0 + dist / 480.0;
      float ga = k * k * (0.3 + 0.5 * ros) * (0.7 + 0.5 * n);
      col = mix(col, vec3(1.0, 0.42, 0.08), ga * (1.0 - alpha));
      alpha = max(alpha, ga);
    }

    alpha *= inside;
    outColor = vec4(col * alpha, alpha); // premultiplied
  }
`;

// Billboards are built in clip space so they keep a minimum on-screen size.
const PARTICLE_VERT = /* glsl */ `
  precision highp float;
  uniform float uClock;
  uniform vec2 uViewport;
  uniform vec3 uWind;      // downwind drift, metres per second (x east, y south)
  uniform float uKind;     // 0 flame, 1 ember, 2 smoke
  in vec3 position;        // quad corner in [-0.5, 0.5]
  in vec3 iPos;            // spawn point, metres from origin (x east, y south, z up)
  in vec3 iVel;
  in vec4 iData;           // birth, life, size, seed
  uniform mat4 uMatrix;
  out vec2 vCorner;
  out float vAge;
  out float vSeed;
  out float vHeight;

  void main() {
    float age = (uClock - iData.x) / iData.y;
    vAge = age;
    vSeed = iData.w;
    vCorner = position.xy;
    if (age < 0.0 || age > 1.0) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }
    float s = uClock - iData.x;
    vec3 p = iPos;
    float size = iData.z;
    if (uKind < 0.5) {
      p += iVel * s + uWind * s * 0.35;
      size *= 1.0 - 0.6 * age;
    } else if (uKind < 1.5) {
      float lift = 1.0 - exp(-s * 0.9);
      p += vec3(iVel.xy * s, iVel.z * lift / 0.9) + uWind * s * (0.6 + lift);
      p.xy += vec2(sin(s * 3.1 + vSeed * 40.0), cos(s * 2.7 + vSeed * 31.0)) * 40.0 * lift;
    } else {
      float rise = sqrt(s) * iVel.z;
      p += vec3(iVel.xy * s, rise) + uWind * s * (0.4 + 0.6 * min(rise / 1500.0, 1.0));
      size *= 0.35 + 1.6 * sqrt(age);
    }
    vHeight = p.z - iPos.z;
    vec4 c = uMatrix * vec4(p, 1.0);
    float pxPerM = length(vec3(uMatrix[0][0], uMatrix[1][0], uMatrix[2][0])) * uViewport.x * 0.5 / c.w;
    float minPx = uKind < 0.5 ? 3.0 : uKind < 1.5 ? 2.2 : 6.0;
    float px = max(size * pxPerM, minPx);
    vec2 stretch = uKind < 0.5 ? vec2(0.7, 1.5) : vec2(1.0);
    c.xy += position.xy * stretch * px * 2.0 / uViewport * c.w;
    gl_Position = c;
  }
`;

const PARTICLE_FRAG = /* glsl */ `
  precision highp float;
  uniform float uKind;
  uniform float uClock;
  uniform float uDark;
  in vec2 vCorner;
  in float vAge;
  in float vSeed;
  in float vHeight;
  out vec4 outColor;
  ${NOISE}

  void main() {
    float r = length(vCorner) * 2.0;
    if (r > 1.0) discard;
    if (uKind < 0.5) {
      // Flame tongue: hot core, ragged top.
      float y = vCorner.y + 0.5;
      float edge = 1.0 - smoothstep(0.35, 1.0, r + (vnoise(vCorner * 6.0 + vec2(vSeed * 9.0, -uClock * 5.0)) - 0.5) * 0.5);
      float h = edge * (1.0 - vAge) * (1.1 - 0.6 * y);
      vec3 c = mix(vec3(0.9, 0.15, 0.02), vec3(1.0, 0.65, 0.15), smoothstep(0.2, 0.7, h));
      c = mix(c, vec3(1.0, 0.95, 0.75), smoothstep(0.75, 1.0, h));
      outColor = vec4(c * h, 0.0); // additive
    } else if (uKind < 1.5) {
      float tw = 0.6 + 0.4 * sin(uClock * 18.0 + vSeed * 60.0);
      float core = exp(-r * r * 6.0);
      float fade = smoothstep(0.0, 0.08, vAge) * (1.0 - smoothstep(0.55, 1.0, vAge));
      vec3 c = mix(vec3(1.0, 0.35, 0.05), vec3(1.0, 0.85, 0.5), core);
      outColor = vec4(c * core * fade * tw * 1.6, 0.0);
    } else {
      float n = fbmN(vCorner * 3.0 + vSeed * 13.0 + vec2(0.0, -vAge * 1.5), 3);
      float puff = smoothstep(1.0, 0.0, r + (n - 0.5) * 0.8);
      puff *= puff;
      float fade = smoothstep(0.0, 0.12, vAge) * (1.0 - smoothstep(0.45, 1.0, vAge));
      float lit = exp(-vHeight / 260.0);   // underside lit by the fire
      vec3 day = mix(vec3(0.38, 0.36, 0.34), vec3(0.82, 0.8, 0.77), smoothstep(0.0, 1.0, vAge + n * 0.3));
      vec3 night = mix(vec3(0.16, 0.14, 0.13), vec3(0.34, 0.33, 0.33), vAge);
      vec3 c = mix(day, night, uDark);
      c = mix(c, vec3(1.0, 0.45, 0.15), lit * (0.25 + 0.45 * uDark));
      float a = puff * fade * mix(0.22, 0.26, uDark);
      outColor = vec4(c * a, a);
    }
  }
`;

// --- particles -------------------------------------------------------------

const KINDS = [
  { n: 3000, life: [0.4, 0.9], size: [45, 110], blend: "add" },     // flames
  { n: 3000, life: [2.5, 5.5], size: [14, 26], blend: "add" },      // embers
  { n: 2600, life: [12, 20], size: [520, 900], blend: "normal" },   // smoke
];

function makeSystem(kind) {
  const spec = KINDS[kind];
  const geo = new THREE.InstancedBufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array([-0.5, -0.5, 0, 0.5, -0.5, 0, 0.5, 0.5, 0, -0.5, 0.5, 0]), 3));
  geo.setIndex([0, 1, 2, 0, 2, 3]);
  const attr = (k) => new THREE.InstancedBufferAttribute(new Float32Array(spec.n * k), k).setUsage(THREE.DynamicDrawUsage);
  const iPos = attr(3), iVel = attr(3), iData = attr(4);
  for (let i = 0; i < spec.n; i++) iData.array[i * 4] = -1e9;
  geo.setAttribute("iPos", iPos);
  geo.setAttribute("iVel", iVel);
  geo.setAttribute("iData", iData);
  geo.instanceCount = spec.n;
  const mat = new THREE.RawShaderMaterial({
    glslVersion: THREE.GLSL3,
    vertexShader: PARTICLE_VERT,
    fragmentShader: PARTICLE_FRAG,
    uniforms: {
      uClock: { value: 0 }, uViewport: { value: new THREE.Vector2(1, 1) }, uWind: { value: new THREE.Vector3() },
      uKind: { value: kind }, uMatrix: { value: new THREE.Matrix4() }, uDark: { value: 1 },
    },
    transparent: true,
    depthTest: true,
    depthWrite: false,
    premultipliedAlpha: true,
    blending: spec.blend === "add" ? THREE.AdditiveBlending : THREE.CustomBlending,
    blendSrc: THREE.OneFactor,
    blendDst: THREE.OneMinusSrcAlphaFactor,
  });
  if (spec.blend === "add") {
    mat.blending = THREE.CustomBlending;
    mat.blendSrc = THREE.OneFactor;
    mat.blendDst = THREE.OneFactor;
  }
  const mesh = new THREE.Mesh(geo, mat);
  mesh.frustumCulled = false;
  mesh.renderOrder = kind === 2 ? 0 : kind === 0 ? 1 : 2;
  return { spec, mesh, mat, iPos, iVel, iData, next: 0, dirty: false };
}

const rand = (a, b) => a + Math.random() * (b - a);

// --- renderer --------------------------------------------------------------

export class FireRenderer {
  constructor(map) {
    this.map = map;
    this.arr = null;
    this.shift = [0, 0];
    this.target = 0;
    this.t = 0;
    this.rate = 0;
    this.visible = true;
    this.smoke = true;
    this.dark = 1;
    this.wind = { dir: 0, mph: 0 };
    this.clock = 0;
    this.last = performance.now();
    this.carry = [0, 0, 0];

    this.canvas = document.createElement("canvas");
    this.canvas.width = this.canvas.height = 2;
    this.ground = new THREE.WebGLRenderer({ canvas: this.canvas, alpha: true, premultipliedAlpha: true, preserveDrawingBuffer: true, antialias: false });
    this.ground.setClearColor(0x000000, 0);
    this.groundMat = new THREE.RawShaderMaterial({
      glslVersion: THREE.GLSL3,
      vertexShader: /* glsl */ `in vec3 position; out vec2 vUv; void main() { vUv = position.xy * 0.5 + 0.5; gl_Position = vec4(position.xy, 0.0, 1.0); }`,
      fragmentShader: GROUND_FRAG,
      uniforms: {
        uField: { value: null }, uSize: { value: new THREE.Vector2() }, uCrop: { value: new THREE.Vector4() },
        uT: { value: 0 }, uClock: { value: 0 }, uHours: { value: 72 }, uMpt: { value: 30 },
        uWind: { value: new THREE.Vector2(0, 1) }, uWindMph: { value: 0 },
      },
      depthTest: false,
      depthWrite: false,
    });
    const quad = new THREE.BufferGeometry();
    quad.setAttribute("position", new THREE.BufferAttribute(new Float32Array([-1, -1, 0, 3, -1, 0, -1, 3, 0]), 3));
    this.groundScene = new THREE.Scene();
    this.groundScene.add(new THREE.Mesh(quad, this.groundMat));
    this.groundCam = new THREE.Camera();

    this.systems = [0, 1, 2].map(makeSystem);
    this.scene = new THREE.Scene();
    for (const s of this.systems) this.scene.add(s.mesh);
    this.camera = new THREE.Camera();
    this.origin = [0, 0];
    this.scale = 1;

    this.tick = this.tick.bind(this);
    this.raf = requestAnimationFrame(this.tick);
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    this.ground.dispose();
    this.groundMat.uniforms.uField.value?.dispose();
    this.air?.dispose();
  }

  /** (Re)install source + layers; call after every style load. */
  attach(slotted) {
    const map = this.map;
    this.air?.dispose();
    this.air = null;
    if (!map.getSource(GROUND)) {
      map.addSource(GROUND, { type: "canvas", canvas: this.canvas, coordinates: this.corners(), animate: false });
    }
    const ground = { id: GROUND, type: "raster", source: GROUND,
                     paint: { "raster-opacity": 1, "raster-fade-duration": 0, "raster-resampling": "linear" } };
    if (slotted) { ground.slot = "middle"; ground.paint["raster-emissive-strength"] = 1; }
    map.addLayer(ground);
    const self = this;
    const air = {
      id: AIR, type: "custom", renderingMode: "3d",
      onAdd(m, gl) {
        self.air = new THREE.WebGLRenderer({ canvas: m.getCanvas(), context: gl, antialias: true });
        self.air.autoClear = false;
      },
      render(gl, matrix) { self.renderAir(matrix); },
      onRemove() { self.air?.dispose(); self.air = null; },
    };
    if (slotted) air.slot = "top";
    map.addLayer(air);
    this.sync();
  }

  corners() {
    const a = this.arr;
    if (!a) return [[0, 0.001], [0.001, 0.001], [0.001, 0], [0, 0]];
    const [x0, y0, x1, y1] = a.crop;
    const ll = (x, y) => {
      const [mx, my] = texelMercator(a, this.shift, x, y);
      const p = new mapboxgl.MercatorCoordinate(mx, my).toLngLat();
      return [p.lng, p.lat];
    };
    return [ll(x0, y0), ll(x1, y0), ll(x1, y1), ll(x0, y1)];
  }

  setData(arr, shift = [0, 0]) {
    if (this.arr === arr && this.shift[0] === shift[0] && this.shift[1] === shift[1]) return;
    const fresh = this.arr !== arr;
    this.arr = arr;
    this.shift = shift;
    if (arr && fresh) {
      const u = this.groundMat.uniforms;
      u.uField.value?.dispose();
      const tex = new THREE.DataTexture(arr.field, arr.W, arr.H, THREE.RGFormat, THREE.FloatType);
      tex.minFilter = tex.magFilter = THREE.NearestFilter;
      tex.needsUpdate = true;
      u.uField.value = tex;
      u.uSize.value.set(arr.W, arr.H);
      const [x0, y0, x1, y1] = arr.crop;
      u.uCrop.value.set(x0, y0, x1, y1);
      u.uHours.value = arr.hours;
      const k = Math.min(2.5, MAX_CANVAS / Math.max(x1 - x0, y1 - y0));
      this.canvas.width = Math.round((x1 - x0) * k);
      this.canvas.height = Math.round((y1 - y0) * k);
      this.ground.setSize(this.canvas.width, this.canvas.height, false);
      this.t = this.target;
    }
    if (arr) {
      // Metres per texel and the local frame the particles live in.
      const [x0, y0, x1, y1] = arr.crop;
      const [ox, oy] = texelMercator(arr, this.shift, (x0 + x1) / 2, (y0 + y1) / 2);
      this.origin = [ox, oy];
      const lat = new mapboxgl.MercatorCoordinate(ox, oy).toLngLat().lat;
      this.scale = mapboxgl.MercatorCoordinate.fromLngLat([0, lat]).meterInMercatorCoordinateUnits();
      const [ax] = texelMercator(arr, this.shift, 0, 0);
      const [bx] = texelMercator(arr, this.shift, arr.W, 0);
      this.groundMat.uniforms.uMpt.value = (bx - ax) / arr.W / this.scale;
      this.clearParticles();
    }
    this.sync();
  }

  /** Fire clock target; while playing, advance smoothly at `rate` hours per second. */
  setHour(hour, rate = 0) {
    if (Math.abs(hour - this.target) > 3) this.clearParticles();
    this.target = hour;
    this.rate = rate;
  }

  setWind(dir, mph) {
    this.wind = { dir: dir ?? 0, mph: mph ?? 0 };
  }

  setTheme(dark) {
    this.dark = dark ? 1 : 0;
  }

  setVisible(visible, smoke = true) {
    this.visible = visible;
    this.smoke = smoke;
    this.sync();
  }

  sync() {
    const map = this.map;
    const src = map.getSource(GROUND);
    if (src) {
      src.setCoordinates(this.corners());
    }
    if (map.getLayer(GROUND)) map.setLayoutProperty(GROUND, "visibility", this.visible && this.arr ? "visible" : "none");
    if (map.getLayer(AIR)) map.setLayoutProperty(AIR, "visibility", this.visible && this.smoke && this.arr ? "visible" : "none");
  }

  clearParticles() {
    for (const s of this.systems) {
      for (let i = 0; i < s.spec.n; i++) s.iData.array[i * 4] = -1e9;
      s.iData.needsUpdate = true;
    }
  }

  // Downwind unit vector, x east / y south (texel space and particle frame agree).
  downwind() {
    const a = ((this.wind.dir + 180) * Math.PI) / 180;
    return [Math.sin(a), -Math.cos(a)];
  }

  tick(now) {
    this.raf = requestAnimationFrame(this.tick);
    const dt = Math.min(0.1, (now - this.last) / 1000);
    this.last = now;
    this.clock += dt;
    // Fire clock: glide toward the target hour; while playing, run ahead of it smoothly.
    if (this.rate > 0 && this.t <= this.target + 1 && this.t >= this.target - 0.5) {
      this.t = Math.min(this.t + this.rate * dt, this.target + 1);
    } else {
      this.t += (this.target - this.t) * (1 - Math.exp(-dt * 7));
    }
    if (!this.arr || !this.visible) return;

    const u = this.groundMat.uniforms;
    const [wx, wy] = this.downwind();
    u.uT.value = this.t;
    u.uClock.value = this.clock;
    u.uWind.value.set(wx, wy);
    u.uWindMph.value = this.wind.mph;
    this.since = (this.since ?? 1) + dt;
    if (this.since >= 1 / GROUND_FPS) {
      this.since = 0;
      this.ground.render(this.groundScene, this.groundCam);
      // Upload exactly one frame: play() marks the source dirty, pause after the map draws it.
      const src = this.map.getSource(GROUND);
      if (src) {
        src.play();
        this.map.once("render", () => src.pause());
      }
    }

    if (this.smoke) this.spawn(dt);
  }

  spawn(dt) {
    const a = this.arr;
    const t = this.t;
    if (t <= 0) return;
    const elev = (lng, lat) => this.map.queryTerrainElevation([lng, lat]) ?? 0;
    const [wx, wy] = this.downwind();
    const mph = this.wind.mph;
    // Live front: cells reached in the last ~20 min; smoke rises from the last 2 h.
    const windows = [[0.3, 1100], [0.6, 260], [2.5, 110]];
    for (let k = 0; k < 3; k++) {
      const s = this.systems[k];
      const lo = lowerBound(a.times, t - windows[k][0]);
      const hi = lowerBound(a.times, t);
      const count = hi - lo;
      if (count <= 0) continue;
      const rate = windows[k][1] * Math.min(1, 0.15 + Math.sqrt(count / 1500));
      this.carry[k] += rate * dt;
      let n = Math.floor(this.carry[k]);
      this.carry[k] -= n;
      while (n-- > 0) {
        const cell = a.order[lo + Math.floor(Math.random() * count)];
        const ros = a.field[cell * 2 + 1];
        if (Math.random() > 0.35 + 0.65 * ros) continue;
        const x = (cell % a.W) + Math.random(), y = Math.floor(cell / a.W) + Math.random();
        const [mx, my] = texelMercator(a, this.shift, x, y);
        const ll = new mapboxgl.MercatorCoordinate(mx, my).toLngLat();
        const i = s.next;
        s.next = (s.next + 1) % s.spec.n;
        s.iPos.array.set([(mx - this.origin[0]) / this.scale, (my - this.origin[1]) / this.scale, elev(ll.lng, ll.lat) + 5], i * 3);
        let v;
        if (k === 0) v = [rand(-15, 15), rand(-15, 15), rand(90, 170) * (0.6 + 0.6 * ros)];
        else if (k === 1) v = [rand(-30, 30) + wx * mph * 4, rand(-30, 30) + wy * mph * 4, rand(220, 520) * (0.6 + ros)];
        else v = [rand(-20, 20), rand(-20, 20), rand(380, 620) * (0.7 + 0.5 * ros)];
        s.iVel.array.set(v, i * 3);
        s.iData.array.set([this.clock, rand(...s.spec.life), rand(...s.spec.size) * (k === 2 ? 0.7 + 0.6 * ros : 1), Math.random()], i * 4);
        s.dirty = true;
      }
    }
    for (const s of this.systems) {
      if (!s.dirty) continue;
      s.iPos.needsUpdate = s.iVel.needsUpdate = s.iData.needsUpdate = true;
      s.dirty = false;
    }
  }

  renderAir(matrix) {
    if (!this.air || !this.arr || !this.visible || !this.smoke) return;
    const model = new THREE.Matrix4().makeTranslation(this.origin[0], this.origin[1], 0)
      .scale(new THREE.Vector3(this.scale, this.scale, this.scale));
    const mvp = new THREE.Matrix4().fromArray(matrix).multiply(model);
    const canvas = this.map.getCanvas();
    const [wx, wy] = this.downwind();
    const drift = 4 + this.wind.mph * 7; // stylised: metres per second of animation
    for (const s of this.systems) {
      const u = s.mat.uniforms;
      u.uMatrix.value.copy(mvp);
      u.uClock.value = this.clock;
      u.uViewport.value.set(canvas.width, canvas.height);
      u.uWind.value.set(wx * drift, wy * drift, 0);
      u.uDark.value = this.dark;
    }
    this.air.resetState();
    this.air.render(this.scene, this.camera);
    this.map.triggerRepaint();
  }
}
