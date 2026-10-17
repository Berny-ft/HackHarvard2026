import { useEffect, useRef } from "react";
import field from "./eaton-field.json";

// The Eaton Fire as a character field: every cell is a 300 m patch of Altadena and the
// San Gabriel foothills, lit at the hour a satellite first saw fire there.
const LOOP_H = 58;          // hours replayed per loop
const LOOP_S = 17;          // seconds per loop
const HOLD_S = 2.6;
const FIRE_COL = 106;       // column of the ignition area
const FIRE_ROW = 21;

const COLORS = {
  flare: [255, 214, 120],
  ember: [255, 106, 43],
  deep: [198, 58, 29],
  ash: [110, 72, 58],
  town: [169, 188, 178],
  wind: [122, 145, 135],
};
const rgba = ([r, g, b], a) => `rgba(${r},${g},${b},${a})`;

// Deterministic per-cell noise so the field never shimmers randomly between frames.
const hash = (i) => {
  let x = (i ^ 0x9e3779b9) * 0x85ebca6b;
  x ^= x >>> 13; x = Math.imul(x, 0xc2b2ae35); x ^= x >>> 16;
  return (x >>> 0) / 4294967295;
};

export default function HeroField({ readoutRef }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    const { cols, rows, arrival, destroyed, standing, wind, acres } = field;
    const start = new Date(field.start);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let W = 0, H = 0, cw = 0, ch = 0, ox = 0, oy = 0;
    let raf = 0, t0 = performance.now(), visible = true, last = 0;

    const fontFor = (size) => `500 ${size}px "JetBrains Mono Variable", ui-monospace, monospace`;

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = canvas.getBoundingClientRect();
      W = rect.width; H = rect.height;
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      // Cover the hero, keeping the fire right of the copy on wide screens.
      cw = Math.max(W / cols, H / rows / 1.6, 9);
      ch = cw * 1.6;
      const anchor = W > 900 ? 0.75 : 0.6;
      ox = Math.min(0, Math.max(W - cols * cw, W * anchor - FIRE_COL * cw));
      // On narrow screens the copy fills the lower half, so lift the fire into the top.
      oy = W > 900 ? (H - rows * ch) / 2 : Math.min(0, H * 0.17 - FIRE_ROW * ch);
      ctx.font = fontFor(Math.round(cw * 1.08));
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.lineWidth = 1.2;
      ctx.lineCap = "round";
    }

    function draw(hour, secs) {
      ctx.clearRect(0, 0, W, H);
      const i0 = Math.max(0, Math.min(wind.length - 1, Math.floor(hour)));
      const [dir, mph, gust] = wind[i0];
      const toward = (dir + 180) % 360;
      const rad = (toward * Math.PI) / 180;
      const vx = Math.sin(rad), vy = -Math.cos(rad);
      const strength = Math.min(1, (mph + gust * 0.5) / 60);

      for (let r = 0; r < rows; r++) {
        const y = oy + r * ch + ch / 2;
        if (y < -ch || y > H + ch) continue;
        for (let c = 0; c < cols; c++) {
          const x = ox + c * cw + cw / 2;
          if (x < -cw || x > W + cw) continue;
          const i = r * cols + c;
          const a = arrival[i];
          const n = hash(i);
          const age = a < 0 ? -1 : hour - a;

          if (a >= 0 && age >= 0) {
            const flicker = 0.75 + 0.25 * Math.sin(secs * 9 + n * 40);
            if (age < 1.5) {
              ctx.fillStyle = rgba(COLORS.flare, 0.95 * flicker);
              ctx.fillText(n > 0.5 ? "#" : "@", x, y);
            } else if (age < 6) {
              ctx.fillStyle = rgba(COLORS.ember, 0.9 * flicker);
              ctx.fillText(n > 0.5 ? "*" : "%", x, y);
            } else if (age < 16) {
              const k = 1 - (age - 6) / 10;
              ctx.fillStyle = rgba(COLORS.deep, 0.45 + 0.4 * k);
              ctx.fillText("+", x, y);
            } else if (destroyed[i]) {
              ctx.fillStyle = rgba(COLORS.deep, 0.62);
              ctx.fillText("x", x, y);
            } else {
              ctx.fillStyle = rgba(COLORS.ash, 0.55);
              ctx.fillText(":", x, y);
            }
            continue;
          }

          const homes = destroyed[i] + standing[i];
          if (homes) {
            ctx.fillStyle = rgba(COLORS.town, Math.min(0.55, 0.16 + homes / 60));
            ctx.fillText(homes > 12 ? "o" : ".", x, y);
            continue;
          }

          // Wind: short strokes in streaks that travel downwind, faster in gusts.
          if (n > 0.22) {
            if ((r + c) % 4 === 0) { ctx.fillStyle = "rgba(122,145,135,0.07)"; ctx.fillText(".", x, y); }
            continue;
          }
          const phase = (c * vx + r * vy * 1.6) * 0.22 - secs * (0.9 + 2.2 * strength);
          const wave = Math.max(0, Math.sin(phase + n * 2.4));
          const alpha = wave * wave * (0.1 + 0.34 * strength);
          if (alpha < 0.03) continue;
          const len = cw * (0.35 + 0.5 * strength);
          ctx.strokeStyle = rgba(COLORS.wind, alpha);
          ctx.beginPath();
          ctx.moveTo(x - vx * len, y - vy * len * 1.0);
          ctx.lineTo(x + vx * len, y + vy * len * 1.0);
          ctx.stroke();
        }
      }

      const ref = readoutRef?.current;
      if (ref) {
        const when = new Date(start.getTime() + hour * 3600e3);
        ref.time.textContent = when.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
        ref.hour.textContent = `T+${hour.toFixed(0)}h`;
        ref.acres.textContent = acres[i0].toLocaleString("en-US");
        ref.wind.textContent = `${Math.round(mph)} mph, gusts ${Math.round(gust)}`;
        ref.arrow.style.transform = `rotate(${toward}deg)`;
      }
    }

    function frame(now) {
      raf = requestAnimationFrame(frame);
      if (!visible || now - last < 33) return;
      last = now;
      const secs = (now - t0) / 1000;
      const cycle = LOOP_S + HOLD_S;
      const s = secs % cycle;
      const hour = Math.min(LOOP_H, (s / LOOP_S) * LOOP_H);
      draw(hour, secs);
      // Brief fade at the loop seam so the restart reads as intentional.
      const fade = s > cycle - 0.6 ? (cycle - s) / 0.6 : s < 0.4 ? s / 0.4 : 1;
      canvas.style.opacity = String(fade);
    }

    const ro = new ResizeObserver(() => { resize(); if (reduced) draw(30, 0); });
    ro.observe(canvas);
    const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; });
    io.observe(canvas);
    resize();

    document.fonts?.ready.then(() => { resize(); if (reduced) draw(30, 0); });
    if (reduced) draw(30, 0);
    else raf = requestAnimationFrame(frame);

    return () => { cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); };
  }, [readoutRef]);

  return <canvas ref={canvasRef} className="hero-field" aria-hidden="true" />;
}
