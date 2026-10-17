import React, { Suspense, lazy, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./theme.css";
import Landing from "./landing/Landing.jsx";

// The map pulls in mapbox-gl; load it only on /map.
const MapApp = lazy(() => import("./App.jsx"));

function Root() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const on = () => setPath(window.location.pathname);
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  if (path.startsWith("/map")) {
    return <Suspense fallback={<div className="app-boot" />}><MapApp /></Suspense>;
  }
  return <Landing />;
}

createRoot(document.getElementById("root")).render(<Root />);
