// Storm surge flooding the coast, rendered from the replay's terrain and the
// tide gauges' water levels.
//
// Ground: a fragment shader floods every cell whose spill level (the lowest
// water that can flow there from the sea) is under the water level at that
// hour, blended between gauges by inverse distance. Depth is level minus
// ground. Drawn on a WebGL canvas Mapbox drapes over the terrain.
// Air: a three.js custom layer sharing Mapbox's GL context draws the
// hurricane's spiral cloud at its real position and size, and rain over the
// city. Positions, size and timing come from the data; the cloud texture and
// ripples are stylised.
import mapboxgl from "mapbox-gl";
import * as THREE from "three";

const GROUND = "surge-ground";
const AIR = "surge-air";
const MAX_CANVAS = 1400;
const GROUND_FPS = 24;
const NEVER = 999;

// --- data ------------------------------------------------------------------

/** Decode flood.png (R,G = 16-bit spill level, B = spill minus ground) into a float field [spill, ground]. */
export async function loadTerrain(base) {
  const meta = await fetch(`${base}/flood.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!meta) return null;
  const img = new Image();
  img.src = `${base}/flood.png`;
  await img.decode();
  const { width: W, height: H } = img;
  const c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0);
  const px = ctx.getImageData(0, 0, W, H).data;
  const lo = meta.spill_min_ft, span = meta.spill_max_ft - meta.spill_min_ft;
  const field = new Float32Array(W * H * 2);
  for (let i = 0; i < W * H; i++) {
    const q = px[i * 4] * 256 + px[i * 4 + 1];
    if (q === 65535) { field[i * 2] = NEVER; field[i * 2 + 1] = NEVER; continue; }
    const spill = lo + (q / 65534) * span;
    field[i * 2] = spill;
    field[i * 2 + 1] = spill - px[i * 4 + 2] * meta.gap_step_ft;
  }
  return { W, H, field, corners: meta.coordinates, resM: meta.res_m };
}

/** Water level (ft) at each field gauge at a fractional hour; offline hours fall back to tide + background. */
export function gaugeLevels(gauges, ids, t) {
  return ids.map((id) => {
    const g = gauges[id];
    const at = (h) => {
      const i = Math.max(0, Math.min(g.observed_ft.length - 1, h));
      return g.observed_ft[i] ?? (g.tide_ft[i] != null ? g.tide_ft[i] + g.background_ft + (g.model_surge_ft?.[i] ?? 0) : null);
    };
    const h0 = Math.floor(t), f = t - h0;
    const a = at(h0), b = at(h0 + 1);
    if (a == null) return b;
    if (b == null) return a;
    return a + (b - a) * f;
  });
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
  uniform sampler2D uField;   // r = spill ft, g = ground ft (999 = water never gets there)
  uniform vec2 uSize;
  uniform vec4 uGauges[4];    // x, y texel; z level ft; w 1 if active
  uniform float uClock;
  uniform float uMpt;         // metres per texel
  uniform vec2 uWind;         // downwind unit vector, texel space
  uniform float uWindMph;
  uniform float uSeaFt;       // ground at or below this is open water
  uniform float uSurge;       // surge above tide at the main gauge, ft
  uniform float uDark;
  in vec2 vUv;
  out vec4 outColor;
  ${NOISE}

  float levelAt(vec2 t) {
    float sw = 0.0, sl = 0.0;
    for (int i = 0; i < 4; i++) {
      if (uGauges[i].w < 0.5) continue;
      vec2 d = t - uGauges[i].xy;
      float w = 1.0 / max(dot(d, d), 1.0);
      sw += w; sl += w * uGauges[i].z;
    }
    return sw > 0.0 ? sl / sw : -99.0;
  }

  // Per texel: soft wetness (level past spill) and depth.
  vec2 cell(ivec2 p, float level) {
    vec2 c = texelFetch(uField, clamp(p, ivec2(0), ivec2(uSize) - 1), 0).rg;
    float wet = smoothstep(-0.15, 0.15, level - c.r) * step(c.r, 500.0);
    return vec2(wet, max(level - c.g, 0.0) * wet);
  }
  vec3 sampleWater(vec2 t, float level) {
    vec2 p = t - 0.5;
    ivec2 i = ivec2(floor(p));
    vec2 f = fract(p);
    vec2 a = cell(i, level), b = cell(i + ivec2(1, 0), level), c = cell(i + ivec2(0, 1), level), d = cell(i + ivec2(1, 1), level);
    vec2 s = mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
    float g = texelFetch(uField, clamp(ivec2(t), ivec2(0), ivec2(uSize) - 1), 0).g;
    return vec3(s.x, s.x > 1e-3 ? s.y / s.x : 0.0, g);
  }

  void main() {
    vec2 t = vec2(vUv.x, 1.0 - vUv.y) * uSize;
    vec2 m = t * uMpt;
    // Organic shoreline: the waterline wobbles a few metres and creeps with the wind.
    vec2 warp = vec2(fbm(m / 90.0 + uClock * 0.05), fbm(m / 90.0 + 11.7 - uClock * 0.04)) - 0.5;
    vec2 tw = t + warp * (36.0 / uMpt);
    float level = levelAt(tw);
    vec3 w = sampleWater(tw, level);
    float wet = w.x, depth = w.y, ground = w.z;
    bool sea = ground <= uSeaFt;

    vec2 flow = m / 55.0 - uWind * uClock * (0.25 + uWindMph * 0.03);
    float n = fbm(flow);
    float ripple = vnoise(m / 9.0 + uWind * uClock * 1.8) * 0.6 + vnoise(m / 4.0 - uClock * 0.7) * 0.4;

    if (sea) {
      // Open water: storm-churned when the surge is up; quiet water is left to the basemap.
      float storm = smoothstep(0.8, 5.0, uSurge) * smoothstep(10.0, 45.0, uWindMph + uSurge * 4.0);
      // Whitecaps and foam streaks drawn out along the wind.
      vec2 across = vec2(-uWind.y, uWind.x);
      vec2 sm = vec2(dot(m, uWind) / 160.0, dot(m, across) / 22.0) - vec2(uClock * (0.4 + uWindMph * 0.03), 0.0);
      float streak = smoothstep(0.62, 0.9, fbmN(sm, 4));
      float swell = 0.5 + 0.5 * sin(dot(m, uWind) / 70.0 - uClock * 1.6 + n * 4.0);
      vec3 col = mix(vec3(0.08, 0.24, 0.3), vec3(0.03, 0.12, 0.18), uDark);
      col = mix(col, col * 1.35, swell * 0.5);
      col = mix(col, vec3(0.88, 0.95, 0.96), streak * 0.7);
      float a = storm * (0.3 + 0.12 * swell + 0.45 * streak);
      outColor = vec4(col * a, a);
      return;
    }
    if (wet <= 1e-3) { outColor = vec4(0.0); return; }

    // Flooded land: sheet water over streets, deepening to open blue.
    float dn = depth + (n - 0.5) * 0.4;
    vec3 shallow = mix(vec3(0.45, 0.8, 0.82), vec3(0.3, 0.7, 0.75), uDark);
    vec3 mid = mix(vec3(0.1, 0.6, 0.78), vec3(0.08, 0.5, 0.7), uDark);
    vec3 deep = mix(vec3(0.05, 0.3, 0.58), vec3(0.04, 0.22, 0.48), uDark);
    vec3 col = mix(shallow, mid, smoothstep(0.2, 2.5, dn));
    col = mix(col, deep, smoothstep(2.5, 8.0, dn));
    // Caustics and wind ripples on the surface.
    float caus = pow(1.0 - abs(sin((fbm(m / 14.0 + uClock * 0.35) - 0.5) * 9.0)), 6.0);
    col += vec3(0.25, 0.45, 0.5) * caus * (0.12 + 0.18 * smoothstep(0.3, 3.0, depth)) * (1.0 - 0.5 * uDark);
    col += vec3(0.6, 0.8, 0.85) * smoothstep(0.55, 0.95, ripple) * 0.12;
    float alpha = mix(0.7, 0.93, smoothstep(0.1, 4.0, depth));
    // Foam where the water is thin: the advancing edge.
    float edge = (1.0 - smoothstep(0.0, 0.35, depth)) + (1.0 - smoothstep(0.0, 0.5, wet)) * 0.6;
    float foam = edge * smoothstep(0.45, 0.8, vnoise(m / 6.0 + uClock * 1.3) * 0.7 + n * 0.5);
    col = mix(col, vec3(0.92, 0.97, 0.98), clamp(foam, 0.0, 1.0) * 0.85);
    alpha = max(alpha, foam * 0.9) * smoothstep(0.0, 0.25, wet);
    outColor = vec4(col * alpha, alpha);
  }
`;

// Hurricane cloud disc: spiral rain bands, the eyewall and the eye, turning counter-clockwise.
const CLOUD_VERT = /* glsl */ `
  precision highp float;
  uniform mat4 uMatrix;
  in vec3 position;
  out vec2 vP;
  void main() { vP = position.xy; gl_Position = uMatrix * vec4(position, 1.0); }
`;
const CLOUD_FRAG = /* glsl */ `
  precision highp float;
  uniform float uClock;
  uniform float uEye;       // eyewall radius / disc radius
  uniform float uStrength;  // 0 depression .. 1 major hurricane
  uniform float uFade;
  uniform float uDark;
  in vec2 vP;               // disc coords, -1..1, x east, y south
  out vec4 outColor;
  ${NOISE}
  void main() {
    vec2 p = vec2(vP.x, -vP.y);
    float r = length(p);
    if (r > 1.0) discard;
    float th = atan(p.y, p.x);
    float spin = uClock * 0.08;
    // Log-spiral bands winding inwards counter-clockwise.
    float arms = sin(th * 2.0 + log(max(r, 0.02)) * 5.5 - spin * 6.0);
    vec2 q = vec2(cos(th + spin + r * 4.0), sin(th + spin + r * 4.0)) * r * 6.0;
    float tex = fbmN(q + vec2(uClock * 0.02, 0.0), 5);
    float band = smoothstep(-0.2, 0.9, arms + (tex - 0.5) * 1.6);
    float core = 1.0 - smoothstep(uEye * 1.2, uEye * 4.5 + 0.08, r);       // dense central overcast
    float body = max(band * (1.0 - smoothstep(0.35, 1.0, r)), core);
    float eye = smoothstep(uEye * 0.55, uEye * 0.95, r);
    float wall = exp(-pow((r - uEye) / (uEye * 0.45 + 0.01), 2.0)) * uStrength;
    float a = clamp(body * (0.55 + 0.75 * tex) * mix(0.7, 1.0, uStrength) + wall * 0.6, 0.0, 0.95) * eye;
    a *= 1.0 - smoothstep(0.75, 1.0, r);
    vec3 day = mix(vec3(0.72, 0.76, 0.8), vec3(0.98, 0.99, 1.0), tex + wall * 0.4);
    vec3 night = mix(vec3(0.16, 0.2, 0.26), vec3(0.42, 0.5, 0.6), tex + wall * 0.4);
    vec3 c = mix(day, night, uDark);
    a *= uFade;
    outColor = vec4(c * a, a);
  }
`;

// Rain: streaks falling through a box over the city, slanted by the wind.
const RAIN_VERT = /* glsl */ `
  precision highp float;
  uniform mat4 uMatrix;
  uniform float uClock;
  uniform vec2 uViewport;
  uniform vec3 uFall;       // metres per second (x east, y south, z up; z negative)
  uniform float uBox;       // half-width, metres
  uniform float uTop;
  in vec3 position;
  in vec4 iSeed;            // x, y in [0,1), phase, length factor
  out float vA;
  out vec2 vC;
  void main() {
    float period = uTop / -uFall.z;
    float s = fract(uClock / period + iSeed.z) * period;
    vec3 p = vec3((iSeed.xy * 2.0 - 1.0) * uBox, uTop) + uFall * s;
    p.xy = mod(p.xy + uBox, 2.0 * uBox) - uBox;
    vec4 a = uMatrix * vec4(p, 1.0);
    vec4 b = uMatrix * vec4(p - uFall * 0.06 * iSeed.w, 1.0);
    vec2 sa = a.xy / a.w, sb = b.xy / b.w;
    vec2 dir = sb - sa;
    vec2 nrm = normalize(vec2(-dir.y, dir.x) * uViewport) / uViewport * 1.2;
    vec2 sp = mix(sa, sb, position.y + 0.5) + nrm * position.x;
    vA = smoothstep(0.0, 0.1, s / period) * (1.0 - smoothstep(0.85, 1.0, s / period));
    vC = position.xy;
    gl_Position = vec4(sp * a.w, a.z, a.w);
  }
`;
const RAIN_FRAG = /* glsl */ `
  precision highp float;
  uniform float uIntensity;
  uniform float uDark;
  in float vA;
  in vec2 vC;
  out vec4 outColor;
  void main() {
    float k = (1.0 - abs(vC.x) * 2.0) * (0.4 + 0.6 * (vC.y + 0.5));
    float a = k * vA * uIntensity * mix(0.32, 0.22, uDark);
    vec3 c = mix(vec3(0.85, 0.9, 0.95), vec3(0.6, 0.7, 0.8), uDark);
    outColor = vec4(c * a, a);
  }
`;

const RAIN_N = 9000;

// --- renderer --------------------------------------------------------------

export class SurgeRenderer {
  constructor(map) {
    this.map = map;
    this.terrain = null;
    this.gauges = null;      // replay gauges.json
    this.ids = [];           // field gauge ids
    this.texels = [];        // their texel positions
    this.series = null;
    this.track = null;       // best track
    this.target = 0;
    this.t = 0;
    this.rate = 0;
    this.visible = true;
    this.air = true;
    this.dark = 1;
    this.clock = 0;
    this.last = performance.now();
    this.wind = { dir: 0, mph: 0 };

    this.canvas = document.createElement("canvas");
    this.canvas.width = this.canvas.height = 2;
    this.ground = new THREE.WebGLRenderer({ canvas: this.canvas, alpha: true, premultipliedAlpha: true, preserveDrawingBuffer: true, antialias: false });
    this.ground.setClearColor(0x000000, 0);
    this.groundMat = new THREE.RawShaderMaterial({
      glslVersion: THREE.GLSL3,
      vertexShader: /* glsl */ `in vec3 position; out vec2 vUv; void main() { vUv = position.xy * 0.5 + 0.5; gl_Position = vec4(position.xy, 0.0, 1.0); }`,
      fragmentShader: GROUND_FRAG,
      uniforms: {
        uField: { value: null }, uSize: { value: new THREE.Vector2() },
        uGauges: { value: [0, 1, 2, 3].map(() => new THREE.Vector4()) },
        uClock: { value: 0 }, uMpt: { value: 40 }, uWind: { value: new THREE.Vector2(0, 1) }, uWindMph: { value: 0 },
        uSeaFt: { value: 0.6 }, uSurge: { value: 0 }, uDark: { value: 1 },
      },
      depthTest: false, depthWrite: false,
    });
    const quad = new THREE.BufferGeometry();
    quad.setAttribute("position", new THREE.BufferAttribute(new Float32Array([-1, -1, 0, 3, -1, 0, -1, 3, 0]), 3));
    this.groundScene = new THREE.Scene();
    this.groundScene.add(new THREE.Mesh(quad, this.groundMat));
    this.groundCam = new THREE.Camera();

    // Cloud disc (unit square, scaled per frame) and rain streaks.
    const disc = new THREE.BufferGeometry();
    disc.setAttribute("position", new THREE.BufferAttribute(new Float32Array([-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0]), 3));
    disc.setIndex([0, 1, 2, 0, 2, 3]);
    this.cloudMat = new THREE.RawShaderMaterial({
      glslVersion: THREE.GLSL3, vertexShader: CLOUD_VERT, fragmentShader: CLOUD_FRAG,
      uniforms: { uMatrix: { value: new THREE.Matrix4() }, uClock: { value: 0 }, uEye: { value: 0.05 },
                  uStrength: { value: 0.5 }, uFade: { value: 1 }, uDark: { value: 1 } },
      transparent: true, depthTest: false, depthWrite: false, side: THREE.DoubleSide,
      blending: THREE.CustomBlending, blendSrc: THREE.OneFactor, blendDst: THREE.OneMinusSrcAlphaFactor,
    });
    this.cloud = new THREE.Mesh(disc, this.cloudMat);
    this.cloud.frustumCulled = false;

    const rain = new THREE.InstancedBufferGeometry();
    rain.setAttribute("position", new THREE.BufferAttribute(new Float32Array([-0.5, -0.5, 0, 0.5, -0.5, 0, 0.5, 0.5, 0, -0.5, 0.5, 0]), 3));
    rain.setIndex([0, 1, 2, 0, 2, 3]);
    const seeds = new Float32Array(RAIN_N * 4);
    for (let i = 0; i < RAIN_N; i++) seeds.set([Math.random(), Math.random(), Math.random(), 0.6 + Math.random() * 0.8], i * 4);
    rain.setAttribute("iSeed", new THREE.InstancedBufferAttribute(seeds, 4));
    rain.instanceCount = RAIN_N;
    this.rainMat = new THREE.RawShaderMaterial({
      glslVersion: THREE.GLSL3, vertexShader: RAIN_VERT, fragmentShader: RAIN_FRAG,
      uniforms: { uMatrix: { value: new THREE.Matrix4() }, uClock: { value: 0 }, uViewport: { value: new THREE.Vector2(1, 1) },
                  uFall: { value: new THREE.Vector3(0, 0, -9) }, uBox: { value: 20000 }, uTop: { value: 2500 },
                  uIntensity: { value: 0 }, uDark: { value: 1 } },
      transparent: true, depthTest: true, depthWrite: false,
      blending: THREE.CustomBlending, blendSrc: THREE.OneFactor, blendDst: THREE.OneMinusSrcAlphaFactor,
    });
    this.rain = new THREE.Mesh(rain, this.rainMat);
    this.rain.frustumCulled = false;
    this.scene = new THREE.Scene();
    this.scene.add(this.cloud);
    this.scene.add(this.rain);
    this.camera = new THREE.Camera();

    this.tick = this.tick.bind(this);
    this.raf = requestAnimationFrame(this.tick);
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    this.ground.dispose();
    this.groundMat.uniforms.uField.value?.dispose();
    this.airRenderer?.dispose();
  }

  attach(slotted) {
    const map = this.map;
    this.airRenderer?.dispose();
    this.airRenderer = null;
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
        self.airRenderer = new THREE.WebGLRenderer({ canvas: m.getCanvas(), context: gl, antialias: true });
        self.airRenderer.autoClear = false;
      },
      render(gl, matrix) { self.renderAir(matrix); },
      onRemove() { self.airRenderer?.dispose(); self.airRenderer = null; },
    };
    if (slotted) air.slot = "top";
    map.addLayer(air);
    this.sync();
  }

  corners() {
    return this.terrain ? this.terrain.corners : [[0, 0.001], [0.001, 0.001], [0.001, 0], [0, 0]];
  }

  /** storm: { terrain, gauges, info, series } or null. */
  setData(storm) {
    if (this.storm === storm) return;
    this.storm = storm;
    const tr = storm?.terrain ?? null;
    if (tr && tr !== this.terrain) {
      const u = this.groundMat.uniforms;
      u.uField.value?.dispose();
      const tex = new THREE.DataTexture(tr.field, tr.W, tr.H, THREE.RGFormat, THREE.FloatType);
      tex.minFilter = tex.magFilter = THREE.NearestFilter;
      tex.needsUpdate = true;
      u.uField.value = tex;
      u.uSize.value.set(tr.W, tr.H);
      // Mercator metres per texel, converted to ground metres at this latitude.
      const lat = (tr.corners[0][1] + tr.corners[2][1]) / 2;
      u.uMpt.value = tr.resM * Math.cos((lat * Math.PI) / 180);
      const k = Math.min(1.5, MAX_CANVAS / Math.max(tr.W, tr.H));
      this.canvas.width = Math.round(tr.W * k);
      this.canvas.height = Math.round(tr.H * k);
      this.ground.setSize(this.canvas.width, this.canvas.height, false);
      this.t = this.target;
    }
    this.terrain = tr;
    if (storm) {
      this.ids = storm.info.field_gauges;
      this.texels = storm.info.terrain.gauge_texels;
      this.groundMat.uniforms.uSeaFt.value = Math.max(0.6, storm.info.high_tide_ft ?? 0.6);
      // Local frame for the rain box: metres from the box centre.
      const { lon, lat } = storm.info.center;
      const o = mapboxgl.MercatorCoordinate.fromLngLat([lon, lat]);
      this.origin = [o.x, o.y];
      this.scale = o.meterInMercatorCoordinateUnits();
    }
    this.sync();
  }

  setHour(hour, rate = 0) {
    this.target = hour;
    this.rate = rate;
  }

  setWind(dir, mph) { this.wind = { dir: dir ?? 0, mph: mph ?? 0 }; }
  setTheme(dark) { this.dark = dark ? 1 : 0; }

  setVisible(water, air = true) {
    this.visible = water;
    this.air = air;
    this.sync();
  }

  sync() {
    const map = this.map;
    map.getSource(GROUND)?.setCoordinates(this.corners());
    if (map.getLayer(GROUND)) map.setLayoutProperty(GROUND, "visibility", this.visible && this.terrain ? "visible" : "none");
    if (map.getLayer(AIR)) map.setLayoutProperty(AIR, "visibility", this.air && this.storm ? "visible" : "none");
  }

  downwind() {
    const a = ((this.wind.dir + 180) * Math.PI) / 180;
    return [Math.sin(a), -Math.cos(a)];
  }

  /** Storm state from the best track at fractional hour t. */
  stormAt(t) {
    const pts = this.storm?.info.best_track;
    if (!pts?.length || t < pts[0].t || t > pts[pts.length - 1].t) return null;
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1];
      if (t >= a.t && t <= b.t) {
        const f = b.t === a.t ? 0 : (t - a.t) / (b.t - a.t);
        const lerp = (k) => (a[k] ?? b[k]) + ((b[k] ?? a[k]) - (a[k] ?? b[k])) * f;
        return { lat: lerp("lat"), lon: lerp("lon"), vmax: lerp("vmax_kt"), rmw: lerp("rmw_nm") || 30,
                 r34: Math.max(...(a.radii?.["34"] ?? [150])) };
      }
    }
    return null;
  }

  tick(now) {
    this.raf = requestAnimationFrame(this.tick);
    const dt = Math.min(0.1, (now - this.last) / 1000);
    this.last = now;
    this.clock += dt;
    if (this.rate > 0 && this.t <= this.target + 1 && this.t >= this.target - 0.5) {
      this.t = Math.min(this.t + this.rate * dt, this.target + 1);
    } else {
      this.t += (this.target - this.t) * (1 - Math.exp(-dt * 7));
    }
    if (!this.storm || !this.terrain || !this.visible) return;

    const u = this.groundMat.uniforms;
    const lv = gaugeLevels(this.storm.gauges, this.ids, Math.max(0, this.t));
    for (let i = 0; i < 4; i++) {
      const g = u.uGauges.value[i];
      if (i < this.ids.length && lv[i] != null) g.set(this.texels[i][0], this.texels[i][1], lv[i], 1);
      else g.set(0, 0, 0, 0);
    }
    const s = this.storm.series[Math.max(0, Math.min(this.storm.series.length - 1, Math.round(this.t)))];
    const [wx, wy] = this.downwind();
    u.uClock.value = this.clock;
    u.uWind.value.set(wx, wy);
    u.uWindMph.value = this.wind.mph;
    u.uSurge.value = Math.max(0, s?.surge_ft ?? 0);
    u.uDark.value = this.dark;
    this.since = (this.since ?? 1) + dt;
    if (this.since >= 1 / GROUND_FPS) {
      this.since = 0;
      this.ground.render(this.groundScene, this.groundCam);
      const src = this.map.getSource(GROUND);
      if (src) {
        src.play();
        this.map.once("render", () => src.pause());
      }
    }
  }

  renderAir(matrix) {
    if (!this.airRenderer || !this.storm || !this.air) return;
    const canvas = this.map.getCanvas();
    const zoom = this.map.getZoom();
    const t = Math.max(0, this.t);
    let any = false;

    // Hurricane: a disc sized by the gale-force radius, at 9 km, fading out as you zoom into the city.
    const st = this.stormAt(t);
    const fade = 1 - Math.min(1, Math.max(0, (zoom - 7.2) / 2.3));
    this.cloud.visible = !!st && fade > 0.01;
    if (this.cloud.visible) {
      const c = mapboxgl.MercatorCoordinate.fromLngLat([st.lon, st.lat], 9000);
      const mpu = c.meterInMercatorCoordinateUnits();
      const R = Math.max(250e3, st.r34 * 1852 * 1.6);
      const model = new THREE.Matrix4().makeTranslation(c.x, c.y, c.z).scale(new THREE.Vector3(R * mpu, R * mpu, 1));
      const u = this.cloudMat.uniforms;
      u.uMatrix.value.fromArray(matrix).multiply(model);
      u.uClock.value = this.clock;
      u.uEye.value = Math.min(0.2, (st.rmw * 1852) / R);
      u.uStrength.value = Math.min(1, Math.max(0.15, (st.vmax - 34) / 90));
      u.uFade.value = fade;
      u.uDark.value = this.dark;
      any = true;
    }

    // Rain over the city while the storm's rain bands are overhead.
    const s = this.storm.series[Math.min(this.storm.series.length - 1, Math.round(t))];
    const near = s?.storm_km != null ? 1 - Math.min(1, Math.max(0, (s.storm_km - 250) / 450)) : 0;
    const intensity = Math.min(1, (s?.rain_in ?? 0) * 5 + near * 0.5);
    this.rain.visible = intensity > 0.03 && zoom > 8.5;
    if (this.rain.visible) {
      const model = new THREE.Matrix4().makeTranslation(this.origin[0], this.origin[1], 0)
        .scale(new THREE.Vector3(this.scale, this.scale, this.scale));
      const [wx, wy] = this.downwind();
      const drift = Math.min(28, this.wind.mph * 0.45);
      const u = this.rainMat.uniforms;
      u.uMatrix.value.fromArray(matrix).multiply(model);
      u.uClock.value = this.clock;
      u.uViewport.value.set(canvas.width, canvas.height);
      u.uFall.value.set(wx * drift, wy * drift, -260);
      u.uBox.value = 26000;
      u.uTop.value = 3200;
      u.uIntensity.value = intensity;
      u.uDark.value = this.dark;
      any = true;
    }
    if (!any) return;
    this.airRenderer.resetState();
    this.airRenderer.render(this.scene, this.camera);
    this.map.triggerRepaint();
  }
}
