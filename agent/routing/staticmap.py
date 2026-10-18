"""A picture of a route, for texts and the route page: Mapbox Static Images with the path drawn on it.

Uses the same public Mapbox token as the web map (VITE_MAPBOX_TOKEN in the repo's .env.local, or
MAPBOX_TOKEN). The image URL doubles as the route page's preview image, so iMessage shows the map under the
link. No token -> None, and the page shows directions only.
"""
import os
from functools import lru_cache
from urllib.parse import quote

from .config import REPO

STYLE = "mapbox/dark-v11"


@lru_cache(maxsize=1)
def token():
    t = os.environ.get("MAPBOX_TOKEN") or os.environ.get("VITE_MAPBOX_TOKEN")
    if t:
        return t
    env = REPO / ".env.local"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("VITE_MAPBOX_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def _encode(points):
    """Google encoded polyline (precision 5) of [(lat, lon), ...]."""
    out, plat, plon = [], 0, 0
    for lat, lon in points:
        ilat, ilon = round(lat * 1e5), round(lon * 1e5)
        for d in (ilat - plat, ilon - plon):
            v = ~(d << 1) if d < 0 else d << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ilat, ilon
    return "".join(out)


def route_image_url(path_lonlat, start, end, hazard="fire", size="800x500", max_points=150):
    """Static map of a route: the path in lime, A at the start, S at the safe end (or B)."""
    tok = token()
    if not tok or not path_lonlat:
        return None
    step = max(1, len(path_lonlat) // max_points)
    pts = [(lat, lon) for lon, lat in path_lonlat[::step]] + [tuple(path_lonlat[-1][::-1])]
    color = "bade61" if hazard == "fire" else "7fe0c4"
    overlay = (f"path-6+{color}-0.95({quote(_encode(pts), safe='')}),"
               f"pin-l-a+{color}({start[1]:.5f},{start[0]:.5f}),pin-l-s+ffffff({end[1]:.5f},{end[0]:.5f})")
    return f"https://api.mapbox.com/styles/v1/{STYLE}/static/{overlay}/auto/{size}@2x?padding=60&access_token={tok}"
