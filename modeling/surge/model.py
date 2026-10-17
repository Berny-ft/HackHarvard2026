"""Parametric storm-surge model at a tide gauge, and its calibration.

Storm surge here is the water level above the predicted tide. Three parts:

  * Inverse barometer: the sea rises ~1 cm per hPa of pressure deficit, with
    the deficit from a Holland (1980) pressure profile at the gauge.
  * Wind setup: wind stress piles water against the shore. Wind comes from a
    Holland wind profile (cyclonic, 20 degree inflow, half the forward motion
    added on the right of track). Only the component blowing toward the shore
    counts, scaled by how wide and shallow the shelf is, since stress spread
    over a shallow shelf raises the water far more than over deep water.
  * Response lag: the sea answers the wind with a delay, an exponential relaxation.

Two fitted parameters are global (wind-setup coefficient and the lag), plus
how strongly shelf width matters. They are grid-searched on the train storms
only, against observed gauge surge, using the best track (a hindcast). The
showcase storms are scored with those parameters, never refitted.

The same model runs forward from any NHC official forecast, starting from the
surge observed at the gauge right now; that is what the sentinel uses.
"""
import json
from datetime import datetime, timedelta

import numpy as np

from .config import GAUGES, OUT, STORMS

KT_TO_MS = 0.514444
NM_TO_KM = 1.852
FT_PER_HPA = 0.0328
P_AMBIENT = 1012.0
HOLLAND_B = 1.3
INFLOW_DEG = 20.0
DEFAULT_RMW_NM = 25.0

DEFAULT_PARAMS = {"k_wind": 0.004, "shelf_exp": 0.5, "tau_h": 3.0}


# ---------------------------------------------------------------- storm track

def dtg_time(dtg):
    return datetime.strptime(dtg, "%Y%m%d%H")


def hours_since(t, start):
    return (t - start).total_seconds() / 3600.0


def pressure_from_wind(vmax_kt):
    """Atkinson-Holliday wind-pressure relation, for forecasts that omit pressure."""
    return P_AMBIENT - (max(vmax_kt, 15.0) / 6.7) ** (1 / 0.644)


def track_points(records, start, rmw_lookup=None):
    """ATCF rows -> sorted list of {t (hours since start), lat, lon, vmax_kt, pmin_mb, rmw_nm, radii}."""
    pts = []
    for r in records:
        t = hours_since(dtg_time(r["dtg"]) + timedelta(hours=r.get("tau", 0)), start)
        rmw = r.get("rmw_nm") or (rmw_lookup(t) if rmw_lookup else None) or DEFAULT_RMW_NM
        pts.append({"t": t, "lat": r["lat"], "lon": r["lon"], "vmax_kt": r["vmax_kt"],
                    "pmin_mb": r.get("pmin_mb") or pressure_from_wind(r["vmax_kt"]), "rmw_nm": rmw,
                    "radii": r.get("radii", {})})
    pts.sort(key=lambda p: p["t"])
    return pts


def state_at(pts, t):
    """Storm state at time t (hours), linear between fixes; None outside the track."""
    if not pts or t < pts[0]["t"] - 1e-6 or t > pts[-1]["t"] + 1e-6:
        return None
    for a, b in zip(pts, pts[1:]):
        if a["t"] <= t <= b["t"]:
            f = 0.0 if b["t"] == a["t"] else (t - a["t"]) / (b["t"] - a["t"])
            s = {k: a[k] + f * (b[k] - a[k]) for k in ("lat", "lon", "vmax_kt", "pmin_mb", "rmw_nm")}
            dt = max(b["t"] - a["t"], 1e-6)
            s["motion"] = _offset_km(a["lat"], a["lon"], b["lat"], b["lon"])
            s["motion"] = (s["motion"][0] / dt, s["motion"][1] / dt)    # km/h east, north
            s["radii"] = a["radii"] if f < 0.5 else b["radii"]
            return s
    p = pts[-1]
    return {**{k: p[k] for k in ("lat", "lon", "vmax_kt", "pmin_mb", "rmw_nm", "radii")}, "motion": (0.0, 0.0)}


def _offset_km(lat0, lon0, lat1, lon1):
    kx = 111.32 * np.cos(np.radians((lat0 + lat1) / 2))
    return (lon1 - lon0) * kx, (lat1 - lat0) * 110.57


# ---------------------------------------------------------------- wind + pressure

def wind_at(s, lat, lon):
    """Surface wind at a point: (speed m/s, direction it blows toward, deg), plus distance km and pressure deficit hPa."""
    dx, dy = _offset_km(s["lat"], s["lon"], lat, lon)
    r = max(np.hypot(dx, dy), 1.0)
    rm = s["rmw_nm"] * NM_TO_KM
    x = (rm / r) ** HOLLAND_B
    vmax = s["vmax_kt"] * KT_TO_MS
    mx, my = s["motion"]
    fwd = np.hypot(mx, my) / 3.6                            # m/s
    v = vmax * np.sqrt(x * np.exp(1 - x))                   # normalized Holland profile
    phi = np.degrees(np.arctan2(dx, dy)) % 360              # bearing of the point from the centre
    toward = (phi - 90.0 - INFLOW_DEG) % 360                # cyclonic, turned in toward the centre
    u, w = v * np.sin(np.radians(toward)), v * np.cos(np.radians(toward))
    # Forward-motion asymmetry: half the translation speed, fading with the wind profile.
    k = 0.5 * (v / max(vmax, 1e-6))
    if fwd > 0:
        u += k * mx / 3.6
        w += k * my / 3.6
    speed = float(np.hypot(u, w))
    deficit = (P_AMBIENT - s["pmin_mb"]) * (1 - np.exp(-x))
    return speed, float(np.degrees(np.arctan2(u, w)) % 360), float(r), float(max(deficit, 0.0))


def forcing(s, gauge, params):
    """Equilibrium surge (ft) the storm state would hold at the gauge."""
    if s is None:
        return 0.0
    speed, toward, r, deficit = wind_at(s, gauge["lat"], gauge["lon"])
    if r > 1500:
        return 0.0
    onshore = speed * np.cos(np.radians(toward - gauge["push_to"]))
    shelf = (gauge["shelf_km"] / 50.0) ** params["shelf_exp"]
    setup = params["k_wind"] * shelf * onshore * abs(onshore)
    return float(FT_PER_HPA * deficit + setup)


def run_surge(pts, gauge, hours, params, t0=0, s0=0.0):
    """Hourly modelled surge (ft) at the gauge for t in [t0, t0+hours], relaxing from s0."""
    a = 1 - np.exp(-1.0 / params["tau_h"])
    out, cur = [], s0
    for i in range(hours + 1):
        if i > 0:
            cur += (forcing(state_at(pts, t0 + i), gauge, params) - cur) * a
        out.append(cur)
    return np.array(out)


# ---------------------------------------------------------------- calibration

def load_storm(key):
    from .fetch import fetch_atcf, fetch_gauges
    s = STORMS[key]
    start = datetime.fromisoformat(s["start"])
    atcf = fetch_atcf(key)
    best = track_points(atcf["best_track"], start)
    return s, start, atcf, best, fetch_gauges(key)


def observed_surge(g, background_hours=12):
    """Observed water minus predicted tide, less the background anomaly of the first hours."""
    obs = np.array([np.nan if v is None else v for v in g["observed_ft"]], float)
    tide = np.array([np.nan if v is None else v for v in g["tide_ft"]], float)
    resid = obs - tide
    bg = np.nanmean(resid[:background_hours]) if np.isfinite(resid[:background_hours]).any() else 0.0
    return resid - bg, float(bg)


def hindcast_scores(key, params):
    s, start, _, best, gauges = load_storm(key)
    rows = {}
    for gid, g in gauges.items():
        obs, _ = observed_surge(g)
        t_first = best[0]["t"]
        sim = run_surge(best, {**GAUGES[gid]}, s["hours"] - max(0, int(np.ceil(t_first))), params,
                        t0=max(0, int(np.ceil(t_first))))
        sim = np.concatenate([np.zeros(s["hours"] + 1 - len(sim)), sim])
        ok = np.isfinite(obs)
        if ok.sum() < 12:
            continue
        rows[gid] = {
            "rmse_ft": float(np.sqrt(np.mean((sim[ok] - obs[ok]) ** 2))),
            "peak_obs_ft": float(np.nanmax(obs)), "peak_sim_ft": float(sim[ok].max()),
            "peak_hour_obs": int(np.nanargmax(np.where(ok, obs, -np.inf))), "peak_hour_sim": int(np.argmax(np.where(ok, sim, -np.inf))),
            "sim": sim.round(2).tolist(),
        }
    return rows


def objective(rows):
    """Mean of RMSE and peak error (ft) over gauges: the shape and the number people act on."""
    if not rows:
        return np.inf
    return float(np.mean([0.5 * r["rmse_ft"] + 0.5 * abs(r["peak_sim_ft"] - r["peak_obs_ft"]) for r in rows.values()]))


def calibrate(train):
    best, best_obj = None, np.inf
    for k in (0.001, 0.0015, 0.002, 0.003, 0.004, 0.005, 0.0065, 0.008, 0.01):
        for e in (0.0, 0.5, 1.0):
            for tau in (1.5, 3.0, 5.0, 8.0):
                p = {"k_wind": k, "shelf_exp": e, "tau_h": tau}
                obj = np.mean([objective(hindcast_scores(key, p)) for key in train])
                if obj < best_obj:
                    best, best_obj = p, obj
    return best, best_obj


def run():
    train = [k for k, v in STORMS.items() if v["role"] == "train"]
    params, obj = calibrate(train)
    print(f"[surge model] fitted on {train}: {params}  (objective {obj:.2f} ft)")
    report = {"params": params, "trained_on": train, "storms": {}}
    for key in STORMS:
        rows = hindcast_scores(key, params)
        report["storms"][key] = {"role": STORMS[key]["role"], "gauges": {
            gid: {k: v for k, v in r.items() if k != "sim"} for gid, r in rows.items()}}
        for gid, r in rows.items():
            print(f"  {key:<13} {GAUGES[gid]['name']:<20} peak obs {r['peak_obs_ft']:5.1f} ft  model {r['peak_sim_ft']:5.1f} ft  "
                  f"rmse {r['rmse_ft']:.2f}  peak hour {r['peak_hour_obs']} vs {r['peak_hour_sim']}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "surge_model.json").write_text(json.dumps(report, indent=1))


def fitted_params():
    p = OUT / "surge_model.json"
    return json.loads(p.read_text())["params"] if p.exists() else DEFAULT_PARAMS
