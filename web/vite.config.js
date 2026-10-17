import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Env vars (VITE_MAPBOX_TOKEN) live in the project-root .env.local.
// /api goes to the sentinel's local demo server (`python -m sentinel demo-server` in agent/);
// DEMO_API points it elsewhere (e.g. a second server on another port).
export default defineConfig({
  plugins: [react()],
  envDir: "..",
  server: { proxy: { "/api": process.env.DEMO_API || "http://127.0.0.1:8787" } },
});
