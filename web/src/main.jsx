import React, { Suspense, lazy, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./theme.css";
import Landing from "./landing/Landing.jsx";

// The maps pull in mapbox-gl; load them only on /map and /surge/map.
const MapApp = lazy(() => import("./App.jsx"));
const SurgeApp = lazy(() => import("./surge/SurgeApp.jsx"));
const SurgeLanding = lazy(() => import("./surge/SurgeLanding.jsx"));

function Root() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const on = () => setPath(window.location.pathname);
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  if (path.startsWith("/surge/map")) {
    return <Suspense fallback={<div className="app-boot surge-boot" />}><SurgeApp /></Suspense>;
  }
  if (path.startsWith("/surge")) {
    return <Suspense fallback={<div className="app-boot surge-boot" />}><SurgeLanding /></Suspense>;
  }
  if (path.startsWith("/map")) {
    return <Suspense fallback={<div className="app-boot" />}><MapApp /></Suspense>;
  }
  return <Landing />;
}

createRoot(document.getElementById("root")).render(<Root />);
