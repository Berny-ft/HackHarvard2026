"""Fire-history patterns for the region: trends, seasons, causes, weather."""
import json

import numpy as np
import pandas as pd

from . import features as F
from .config import DATA, OUT
from .fetch import weather_points
from .fireweather import OFFSHORE_FROM_DEG, TIER_NAMES, TIERS, santa_ana_level

CAUSES = {
    1: "Lightning", 2: "Equipment use", 3: "Smoking", 4: "Campfire", 5: "Debris burning",
    6: "Railroad", 7: "Arson", 8: "Playing with fire", 9: "Miscellaneous", 10: "Vehicle",
    11: "Power line", 12: "Firefighter training", 13: "Non-firefighter training",
    14: "Unknown", 15: "Structure", 16: "Aircraft", 18: "Escaped prescribed burn",
    19: "Illegal campfire",
}


def _santa_ana(df):
    return santa_ana_level(df["wind_direction_10m_dominant"], df["wind_gusts_10m_max"],
                           df["relative_humidity_2m_min"])


def fire_weather(fires):
    wx = pd.read_parquet(DATA / "weather_daily.parquet")
    wx["level"] = _santa_ana(wx)
    pts = np.array(weather_points())
    c = fires.to_crs("EPSG:4326").geometry.representative_point()
    nearest = np.argmin((c.x.to_numpy()[:, None] - pts[None, :, 0]) ** 2 +
                        (c.y.to_numpy()[:, None] - pts[None, :, 1]) ** 2, axis=1)
    f = fires.assign(point=nearest, date=fires["alarm"])
    f = f[f["date"] >= "2000-01-01"].merge(wx, left_on=["point", "date"], right_on=["point", "time"])
    large = f[f["GIS_ACRES"] >= 1000]
    tiers = {}
    for lvl, (gust, rh) in TIERS.items():
        on_days, on_fires = wx["level"] >= lvl, large["level"] >= lvl
        base = float(on_days.mean())
        tiers[TIER_NAMES[lvl]] = {
            "rule": f"wind from {OFFSHORE_FROM_DEG[0]}-{OFFSHORE_FROM_DEG[1]} deg, gusts >= {gust} mph, min RH <= {rh}%",
            "share_of_days": base,
            "days_per_year": round(base * 365, 1),
            "share_of_1000ac_fire_starts": float(on_fires.mean()),
            "share_of_large_fire_acres": float(large.loc[on_fires, "GIS_ACRES"].sum() / large["GIS_ACRES"].sum()),
            "lift": float(on_fires.mean() / base) if base else None,
            "days_by_month": {int(m): round(float(v), 3)
                              for m, v in on_days.groupby(wx["time"].dt.month).mean().items()},
        }
    return {
        "tiers": tiers,
        "fire_start_conditions": {
            "median_rh_min": float(f["relative_humidity_2m_min"].median()),
            "median_gust_mph": float(f["wind_gusts_10m_max"].median()),
            "median_temp_max_f": float(f["temperature_2m_max"].median()),
            "large_fire_median_rh_min": float(large["relative_humidity_2m_min"].median()),
            "large_fire_median_gust_mph": float(large["wind_gusts_10m_max"].median()),
            "all_days_median_rh_min": float(wx["relative_humidity_2m_min"].median()),
            "all_days_median_gust_mph": float(wx["wind_gusts_10m_max"].median()),
        },
    }


def landscape_burn_rates(since=2000):
    static, land, prof = F.static_features("region")
    h = np.load(DATA / "region_burn_history.npz")
    burned = F.burned_between(h["years"], h["stack"], since - 1, 2025)
    out = {"fuel_group": {}, "slope_deg": {}, "elevation_m": {}}
    for name, gid in F.FUEL_GROUP_IDS.items():
        m = land & (static["fuel_group"] == gid)
        if m.sum() > 500:
            out["fuel_group"][name] = round(float(burned[m].mean()), 4)
    burnable = land & (static["fuel_group"] >= F.FUEL_GROUP_IDS["grass"])
    for key, edges in (("slope_deg", [0, 5, 10, 20, 30, 90]), ("elevation_m", [0, 250, 500, 1000, 1500, 2000, 4000])):
        v = static[key.split("_")[0]]
        for lo, hi in zip(edges[:-1], edges[1:]):
            m = burnable & (v >= lo) & (v < hi)
            if m.sum() > 500:
                out[key][f"{lo}-{hi}"] = round(float(burned[m].mean()), 4)
    out["note"] = f"share of cells burned at least once {since}-2025 (burnable fuels only for terrain bins)"
    return out


def run():
    fires = F.load_perimeters()
    fires = fires[fires["year"] >= 1950]
    acres = fires.groupby("year")["GIS_ACRES"].agg(["count", "sum"]).reindex(range(1950, 2026), fill_value=0)
    recent = fires[fires["year"] >= 1990]
    month = recent["alarm"].dt.month
    cause = recent["CAUSE"].map(lambda c: CAUSES.get(int(c), "Unknown") if pd.notna(c) else "Unknown")
    top = fires.nlargest(15, "GIS_ACRES")
    patterns = {
        "region": "Los Angeles + Ventura counties",
        "source": "CAL FIRE FRAP fire perimeters",
        "n_fires_since_1950": int(len(fires)),
        "yearly": [{"year": int(y), "fires": int(r["count"]), "acres": round(float(r["sum"]))} for y, r in acres.iterrows()],
        "monthly_since_1990": [{"month": int(m), "fires": int((month == m).sum()),
                                "acres": round(float(recent.loc[month == m, "GIS_ACRES"].sum()))} for m in range(1, 13)],
        "causes_since_1990": (recent.assign(c=cause).groupby("c")["GIS_ACRES"].agg(["count", "sum"])
                              .sort_values("sum", ascending=False)
                              .apply(lambda r: {"fires": int(r["count"]), "acres": round(float(r["sum"]))}, axis=1).to_dict()),
        "largest_fires": [{"name": str(r.FIRE_NAME).title(), "year": int(r.year), "acres": round(float(r.GIS_ACRES)),
                           "alarm": r.alarm.date().isoformat() if pd.notna(r.alarm) else None,
                           "cause": CAUSES.get(int(r.CAUSE), "Unknown") if pd.notna(r.CAUSE) else "Unknown"}
                          for r in top.itertuples()],
        "fire_weather": fire_weather(fires),
        "landscape": landscape_burn_rates(),
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "patterns.json").write_text(json.dumps(patterns, indent=1))

    hist = fires[["FIRE_NAME", "year", "GIS_ACRES", "CAUSE", "alarm", "geometry"]].copy()
    hist["alarm"] = hist["alarm"].dt.date.astype(str)
    hist["geometry"] = hist.geometry.simplify(60)
    hist.to_crs("EPSG:4326").to_file(OUT / "fire_history.geojson", driver="GeoJSON")
    print(f"[patterns] {len(fires)} fires since 1950")
    for name, t in patterns["fire_weather"]["tiers"].items():
        print(f"  {name}: {t['days_per_year']} days/yr, {t['share_of_large_fire_acres']:.0%} of large-fire acres, "
              f"lift {t['lift']:.1f}x")
    print("  burn rate by fuel:", patterns["landscape"]["fuel_group"])
    print("  burn rate by slope:", patterns["landscape"]["slope_deg"])
    print("  top causes:", list(patterns["causes_since_1990"].items())[:4])
