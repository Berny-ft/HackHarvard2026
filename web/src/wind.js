// Decorative wind particles drawn on a canvas above the map.
// Direction and speed follow the data; the flow pattern itself is stylised
// (a little curl noise so it looks alive), not a physical wind field.

// Meteorological "from" direction (deg) + mph -> screen vector, given map bearing/pitch.
function toScreen(fromDeg, mph, bearing, pitch) {
  const a = ((fromDeg + 180 - bearing) * Math.PI) / 180;
  const k = 0.05 * mph + 0.35; // px per frame
  return { u: Math.sin(a) * k, v: -Math.cos(a) * k * Math.max(0.35, Math.cos((pitch * Math.PI) / 180)) };
}

function mix(a, b, t) {
  return a + (b - a) * t;
}

export class WindAnimator {
  constructor(map, canvas) {
    this.map = map;
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.particles = [];
    this.uniform = null; // { dir, mph, gust }
    this.stations = []; // [{ lon, lat, dir, mph, gust, x, y }]
    this.dark = true;
    this.visible = true;
    this.t = 0;
    this.reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    this.resize = this.resize.bind(this);
    this.frame = this.frame.bind(this);
    this.onMove = () => {
      this.project();
      this.clear();
    };
    map.on("move", this.onMove);
    map.on("resize", this.resize);
    this.resize();
    this.raf = requestAnimationFrame(this.frame);
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    this.map.off("move", this.onMove);
    this.map.off("resize", this.resize);
  }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    const { clientWidth: w, clientHeight: h } = this.map.getContainer();
    this.w = w;
    this.h = h;
    this.canvas.width = w * dpr;
    this.canvas.height = h * dpr;
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = Math.round((w * h) / (this.reduced ? 2400 : 900));
    this.particles = Array.from({ length: n }, () => this.spawn({}));
    this.project();
  }

  spawn(p) {
    p.x = Math.random() * this.w;
    p.y = Math.random() * this.h;
    p.age = 0;
    p.life = 50 + Math.random() * 110;
    p.seed = Math.random() * 1000;
    return p;
  }

  clear() {
    this.ctx.clearRect(0, 0, this.w, this.h);
  }

  // One wind value for the whole view (incident mode).
  setUniform(dir, mph, gust) {
    this.uniform = { dir, mph, gust: gust ?? mph };
    this.stations = [];
  }

  // Spatially varying wind from point observations (live mode).
  setStations(rows) {
    this.uniform = null;
    this.stations = rows.map((r) => ({ lon: r.lon, lat: r.lat, dir: r.dir, mph: r.mph, gust: r.gust ?? r.mph }));
    this.project();
  }

  setTheme(dark) {
    this.dark = dark;
  }

  setVisible(v) {
    this.visible = v;
    if (!v) this.clear();
  }

  project() {
    for (const s of this.stations) {
      const pt = this.map.project([s.lon, s.lat]);
      s.x = pt.x;
      s.y = pt.y;
    }
  }

  // Inverse-distance blend of stations in screen space.
  sample(x, y) {
    if (this.uniform) return this.uniform;
    let w = 0, sx = 0, sy = 0, mph = 0, gust = 0;
    for (const s of this.stations) {
      const d2 = (s.x - x) ** 2 + (s.y - y) ** 2 + 400;
      const k = 1 / (d2 * d2);
      const a = (s.dir * Math.PI) / 180;
      sx += Math.sin(a) * k;
      sy += Math.cos(a) * k;
      mph += s.mph * k;
      gust += s.gust * k;
      w += k;
    }
    if (!w) return null;
    return { dir: ((Math.atan2(sx, sy) * 180) / Math.PI + 360) % 360, mph: mph / w, gust: gust / w };
  }

  frame() {
    this.raf = requestAnimationFrame(this.frame);
    if (!this.visible || (!this.uniform && !this.stations.length)) return;
    const ctx = this.ctx;
    this.t += 1;

    // Fade previous trails.
    ctx.globalCompositeOperation = "destination-in";
    ctx.fillStyle = "rgba(0,0,0,0.94)";
    ctx.fillRect(0, 0, this.w, this.h);
    ctx.globalCompositeOperation = "source-over";

    const bearing = this.map.getBearing();
    const pitch = this.map.getPitch();
    const slow = this.reduced ? 0.35 : 1;
    ctx.lineWidth = 1;
    ctx.lineCap = "round";

    for (const p of this.particles) {
      const s = this.sample(p.x, p.y);
      if (!s) continue;
      // Gusts: speed pulses between sustained and gust, out of phase across the view.
      const pulse = 0.5 + 0.5 * Math.sin(this.t * 0.03 + p.seed + p.x * 0.004);
      const mph = mix(s.mph, s.gust, pulse * 0.6);
      // Gentle curl so streaks meander instead of moving in rigid lines.
      const wobble = 18 * Math.sin(p.x * 0.006 + this.t * 0.004) * Math.cos(p.y * 0.007 - this.t * 0.003);
      const { u, v } = toScreen(s.dir + wobble, mph, bearing, pitch);
      const nx = p.x + u * slow;
      const ny = p.y + v * slow;

      // Fade in/out over the particle's life; warmer + brighter in strong wind.
      const life = Math.sin((p.age / p.life) * Math.PI);
      const heat = Math.min(1, Math.max(0, (mph - 10) / 40));
      const alpha = (0.1 + 0.15 * Math.min(1, mph / 30)) * life;
      ctx.strokeStyle = this.dark
        ? `rgba(${Math.round(mix(225, 255, heat))},${Math.round(mix(238, 196, heat))},${Math.round(mix(255, 120, heat))},${alpha})`
        : `rgba(${Math.round(mix(40, 190, heat))},${Math.round(mix(60, 80, heat))},${Math.round(mix(90, 30, heat))},${alpha * 0.85})`;
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(nx, ny);
      ctx.stroke();

      p.x = nx;
      p.y = ny;
      p.age += 1;
      if (p.age > p.life || nx < -10 || ny < -10 || nx > this.w + 10 || ny > this.h + 10) this.spawn(p);
    }
  }
}

