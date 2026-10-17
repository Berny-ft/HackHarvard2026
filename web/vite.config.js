import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Env vars (VITE_MAPBOX_TOKEN) live in the project-root .env.local.
// /api goes to the sentinel's local demo server (`python -m sentinel demo-server` in agent/).
export default defineConfig({
  plugins: [react()],
  envDir: "..",
  server: { proxy: { "/api": "http://127.0.0.1:8787" } },
});
