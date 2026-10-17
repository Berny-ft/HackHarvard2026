import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { createReadStream, existsSync, statSync } from "fs";
import path from "path";
import { fileURLToPath } from "url";

// Env vars (VITE_MAPBOX_TOKEN) live in the project-root .env.local.
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const dataRoot = path.resolve(__dirname, "../modeling/out");

const contentTypes = {
  ".json": "application/json",
  ".geojson": "application/geo+json",
  ".png": "image/png",
  ".tif": "image/tiff",
  ".tiff": "image/tiff",
};

// web/public/data is a symlink to ../../modeling/out in git. On machines
// checked out with core.symlinks=false (common on Windows), that symlink
// lands as a plain text file instead of a directory, so Vite's static
// public-dir serving can't follow it. Serve /data from modeling/out
// directly instead, with the real symlink left in place for POSIX clones.
function serveModelingOut() {
  const middleware = (req, res, next) => {
    const urlPath = decodeURIComponent(req.url.split("?")[0]);
    const resolved = path.resolve(dataRoot, "." + urlPath);
    if (!resolved.startsWith(dataRoot)) {
      res.statusCode = 404;
      res.end();
      return;
    }
    if (!existsSync(resolved) || !statSync(resolved).isFile()) {
      next();
      return;
    }
    const ext = path.extname(resolved).toLowerCase();
    res.setHeader("Content-Type", contentTypes[ext] || "application/octet-stream");
    createReadStream(resolved).pipe(res);
  };
  return {
    name: "serve-modeling-out",
    configureServer(server) {
      server.middlewares.use("/data", middleware);
    },
    configurePreviewServer(server) {
      server.middlewares.use("/data", middleware);
    },
  };
}

// /api goes to the sentinel's local demo server (`python -m sentinel demo-server` in agent/);
// DEMO_API points it elsewhere (e.g. a second server on another port).
export default defineConfig({
  plugins: [react(), serveModelingOut()],
  envDir: "..",
  server: { proxy: { "/api": process.env.DEMO_API || "http://127.0.0.1:8787" } },
});
