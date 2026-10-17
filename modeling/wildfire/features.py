"""Per-cell terrain, fuel and fire-history features on a fixed grid."""
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from scipy import ndimage

from .config import CRS, DATA

# Scott & Burgan 40 fuel model codes, grouped by carrier.
FUEL_GROUPS = {
    "urban": [91],
    "nonburnable": [92, 93, 98, 99],
    "grass": list(range(101, 110)),
    "grass_shrub": list(range(121, 125)),
    "shrub": list(range(141, 150)),
    "timber_understory": list(range(161, 166)),
    "timber_litter": list(range(181, 190)),
    "slash": list(range(201, 205)),
}
FUEL_GROUP_IDS = {name: i for i, name in enumerate(FUEL_GROUPS)}


def load_raster(path):
    with rasterio.open(path) as src:
        return src.read(1), src.profile


def fuel_group(fbfm):
    g = np.full(fbfm.shape, -1, dtype=np.int8)  # -1 = ocean / no data
    for name, codes in FUEL_GROUPS.items():
        g[np.isin(fbfm, codes)] = FUEL_GROUP_IDS[name]
    return g


def terrain(dem, res):
    dem = np.where(np.isnan(dem), 0, dem)
    dzdy, dzdx = np.gradient(dem, res)  # rows run north -> south
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    # Aspect = compass direction the slope faces (downhill).
    aspect = (np.degrees(np.arctan2(-dzdx, dzdy)) + 360) % 360
    win = max(3, int(round(1500 / res)) | 1)
    tpi = dem - ndimage.uniform_filter(dem, size=win)
    return slope.astype(np.float32), aspect.astype(np.float32), tpi.astype(np.float32)


def load_perimeters():
    gdf = gpd.read_file(DATA / "frap_perimeters.geojson")
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf["geometry"] = gdf.geometry.make_valid()
    gdf["year"] = pd.to_numeric(gdf["YEAR_"], errors="coerce")
    gdf = gdf[gdf["year"].notna()]
    gdf["year"] = gdf["year"].astype(int)
    # FRAP stores calendar dates as midnight UTC; keep them as naive dates.
    gdf["alarm"] = pd.to_datetime(gdf["ALARM_DATE"], unit="ms", errors="coerce").dt.normalize()
    return gdf.to_crs(CRS)


def burn_history(perims, profile, start_year=1950, end_year=2025):
    """Return a (years, H, W) bool stack: cell burned in that year."""
    years = np.arange(start_year, end_year + 1)
    shape = (profile["height"], profile["width"])
    stack = np.zeros((len(years), *shape), dtype=bool)
    for i, y in enumerate(years):
        geoms = perims.loc[perims["year"] == y, "geometry"]
        if len(geoms):
            stack[i] = rasterize(
                ((g, 1) for g in geoms), out_shape=shape,
                transform=profile["transform"], fill=0, dtype="uint8",
            ).astype(bool)
    return years, stack


def history_features(years, stack, through_year):
    """Times burned and years-since-last-burn using fires up to `through_year`."""
    sel = years <= through_year
    ys, st = years[sel], stack[sel]
    count = st.sum(axis=0).astype(np.int16)
    last = np.where(st.any(axis=0), ys[::-1][np.argmax(st[::-1], axis=0)], 0)
    since = np.where(last > 0, through_year - last, 100).clip(0, 100).astype(np.int16)
    return count, since


def burned_between(years, stack, after, through):
    sel = (years > after) & (years <= through)
    return stack[sel].any(axis=0)


def static_features(prefix="region"):
    dem, prof = load_raster(DATA / f"{prefix}_dem.tif")
    fbfm, _ = load_raster(DATA / f"{prefix}_fbfm40.tif")
    cc, _ = load_raster(DATA / f"{prefix}_cc.tif")
    res = prof["transform"].a
    slope, aspect, tpi = terrain(dem, res)
    group = fuel_group(fbfm)
    land = group >= 0
    urban = group == FUEL_GROUP_IDS["urban"]
    px = res / 1000
    dist_urban = ndimage.distance_transform_edt(~urban) * px
    dist_coast = ndimage.distance_transform_edt(land) * px
    feats = {
        "elevation": np.nan_to_num(dem).astype(np.float32),
        "slope": slope,
        "northness": np.cos(np.radians(aspect)).astype(np.float32),
        "eastness": np.sin(np.radians(aspect)).astype(np.float32),
        "tpi": tpi,
        "fuel_group": group.astype(np.float32),
        "fbfm40": fbfm.astype(np.float32),
        "canopy_cover": np.where(cc < 0, 0, cc).astype(np.float32),
        "dist_urban_km": dist_urban.astype(np.float32),
        "dist_coast_km": dist_coast.astype(np.float32),
    }
    return feats, land, prof
