import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Env vars (VITE_MAPBOX_TOKEN) live in the project-root .env.local.
export default defineConfig({ plugins: [react()], envDir: ".." });
