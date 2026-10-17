import { useEffect, useRef } from "react";
import field from "./sandy-field.json";

// Hurricane Sandy over New York Harbor as a character field: every cell is a
// ~300 m patch of the harbour, lit at the hour the tide gauges' observed water
// stood 6 inches deep there. The sea swells with the measured water level.
const FROM_H = 40;          // replay hours shown per loop (the run-up to the peak and the drain)
const TO_H = 84;
const LOOP_S = 16;
const HOLD_S = 2.4;
const HARBOR_COL = 70;      // column of the Upper Bay

const COLORS = {
  sea: [56, 132, 178],
  crest: [210, 240, 250],
  land: [166, 192, 207],
  wet: [26, 166, 196],
  foam: [233, 247, 255],
  wind: [111, 142, 161],
};
const rgba = ([r, g, b], a) => `rgba(${r},${g},${b},${a})`;
const hash = (i) => {
  let x = (i ^ 0x9e3779b9) * 0x85ebca6b;
  x ^= x >>> 13; x = Math.imul(x, 0xc2b2ae35); x ^= x >>> 16;
  return (x >>> 0) / 4294967295;
};

export default function SurgeField({ readoutRef }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    const { cols, rows, kind, arrival, depth, water, wind, acres, storm_km: stormKm, peak_hour: peakH } = field;
    const start = new Date(field.start);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let W = 0, H = 0, cw = 0, ch = 0, ox = 0, oy = 0;
    let raf = 0, t0 = performance.now(), visible = true, last = 0;

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = canvas.getBoundingClientRect();
      W = rect.width; H = rect.height;
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      cw = Math.max(W / cols, H / rows / 1.6, 9);
      ch = cw * 1.6;
      const anchor = W > 900 ? 0.72 : 0.55;
      ox = Math.min(0, Math.max(W - cols * cw, W * anchor - HARBOR_COL * cw));
      oy = (H - rows * ch) / 2;
      ctx.font = `500 ${Math.round(cw * 1.08)}px "JetBrains Mono Variable", ui-monospace, monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.lineWidth = 1.2;
      ctx.lineCap = "round";
    }

    const at = (arr, hour) => {
      const i = Math.max(0, Math.min(arr.length - 1, Math.floor(hour)));
      const j = Math.min(arr.length - 1, i + 1), f = hour - Math.floor(hour);
      const a = arr[i] ?? arr[j] ?? 0, b = arr[j] ?? a;
      return a + (b - a) * f;
    };

    function draw(hour, secs) {
      ctx.clearRect(0, 0, W, H);
      const i0 = Math.max(0, Math.min(wind.length - 1, Math.floor(hour)));
      const [dir, mph, gust] = wind[i0];
      const toward = ((dir ?? 0) + 180) % 360;
      const rad = (toward * Math.PI) / 180;
      const vx = Math.sin(rad), vy = -Math.cos(rad);
      const strength = Math.min(1, ((mph ?? 0) + (gust ?? 0) * 0.5) / 60);
      const level = at(water, hour);
      const swell = Math.min(1, Math.max(0, (level - 2) / 9));     // 0 at a normal tide, 1 at Sandy's peak

      for (let r = 0; r < rows; r++) {
        const y = oy + r * ch + ch / 2;
        if (y < -ch || y > H + ch) continue;
        for (let c = 0; c < cols; c++) {
          const x = ox + c * cw + cw / 2;
          if (x < -cw || x > W + cw) continue;
          const i = r * cols + c;
          const n = hash(i);
          const k = kind[i];

          if (k === 0) {
            // Sea: waves travel downwind; taller, brighter and more of them as the surge comes in.
            const phase = (c * vx + r * vy * 1.6) * 0.32 - secs * (1.1 + 2.4 * strength);
            const wave = Math.max(0, Math.sin(phase + n * 3.1));
            const crest = wave > 0.93 - 0.25 * swell;
            ctx.fillStyle = crest ? rgba(COLORS.crest, 0.45 + 0.5 * swell) : rgba(COLORS.sea, 0.4 + 0.3 * wave + 0.15 * swell);
            ctx.fillText(crest ? "≈" : n > 0.5 ? "~" : "-", x, y);
            continue;
          }

          const a = arrival[i];
          const age = a < 0 ? -1 : hour - a;
          if (a >= 0 && age >= 0) {
            // Flooded: the arriving edge foams, then the water settles to a depth-coloured sheet.
            const d = Math.min(1, depth[i] / 6) * Math.min(1, swell * 1.4 + 0.15);
            const shimmer = 0.8 + 0.2 * Math.sin(secs * 3 + n * 30);
            if (age < 1.2) {
              ctx.fillStyle = rgba(COLORS.foam, 0.95 * shimmer);
              ctx.fillText(n > 0.5 ? "≈" : "~", x, y);
            } else if (level > 4 || age < 14) {
              const col = d > 0.55 ? COLORS.foam : COLORS.wet;
              ctx.fillStyle = rgba(col, (0.55 + 0.4 * d) * shimmer);
              ctx.fillText(d > 0.25 ? "≈" : "~", x, y);
            } else {
              ctx.fillStyle = rgba(COLORS.wet, 0.3);
              ctx.fillText(":", x, y);           // drained, sodden ground
            }
            continue;
          }
          if (k === 2) {
            // Will flood: low land, faintly marked so the eye finds it before the water does.
            ctx.fillStyle = rgba(COLORS.land, 0.16);
            ctx.fillText(n > 0.5 ? "o" : ".", x, y);
            continue;
          }
          if (n > 0.25) {
            if ((r + c) % 3 === 0) { ctx.fillStyle = rgba(COLORS.land, 0.07); ctx.fillText(".", x, y); }
            continue;
          }
          const phase = (c * vx + r * vy * 1.6) * 0.22 - secs * (0.9 + 2.2 * strength);
          const wave = Math.max(0, Math.sin(phase + n * 2.4));
          const alpha = wave * wave * (0.08 + 0.3 * strength);
          if (alpha < 0.03) continue;
          const len = cw * (0.35 + 0.5 * strength);
          ctx.strokeStyle = rgba(COLORS.wind, alpha);
          ctx.beginPath();
          ctx.moveTo(x - vx * len, y - vy * len);
          ctx.lineTo(x + vx * len, y + vy * len);
          ctx.stroke();
        }
      }

      const ref = readoutRef?.current;
      if (ref) {
        const when = new Date(start.getTime() + hour * 3600e3);
        ref.time.textContent = when.toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
        const toPeak = hour - peakH;
        ref.hour.textContent = `${toPeak < 0 ? "−" : "+"}${Math.abs(toPeak).toFixed(0)}h`;
        ref.water.textContent = `${level.toFixed(1)} ft`;
        ref.acres.textContent = Math.round(acres[i0]).toLocaleString("en-US");
        ref.storm.textContent = stormKm[i0] != null ? `${Math.round(stormKm[i0] * 0.621)} mi` : "–";
      }
    }

    function frame(now) {
      raf = requestAnimationFrame(frame);
      if (!visible || now - last < 33) return;
      last = now;
      const secs = (now - t0) / 1000;
      const cycle = LOOP_S + HOLD_S;
      const s = secs % cycle;
      const hour = FROM_H + Math.min(1, s / LOOP_S) * (TO_H - FROM_H);
      draw(hour, secs);
      const fade = s > cycle - 0.6 ? (cycle - s) / 0.6 : s < 0.4 ? s / 0.4 : 1;
      canvas.style.opacity = String(fade);
    }

    const ro = new ResizeObserver(() => { resize(); if (reduced) draw(peakH, 0); });
    ro.observe(canvas);
    const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; });
    io.observe(canvas);
    resize();
    document.fonts?.ready.then(() => { resize(); if (reduced) draw(peakH, 0); });
    if (reduced) draw(peakH, 0);
    else raf = requestAnimationFrame(frame);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); };
  }, [readoutRef]);

  return <canvas ref={canvasRef} className="hero-field" aria-hidden="true" />;
}
