"""Fire spread simulation: Rothermel rate of spread + minimum travel time.

Every hour the weather (wind, temperature, humidity) updates the dead fuel
moisture and wind, which changes each cell's elliptical spread rate. Fire
arrival time is solved with a time-dependent Dijkstra over a 16-neighbour
grid (the same idea as FlamMap's MTT algorithm, simplified).
"""
import heapq
import math

import numba
import numpy as np
import pandas as pd

from .rothermel import FUEL_MODELS, fosberg_emc, fuel_bed

FT_PER_M = 3.28084
URBAN = 91  # LANDFIRE "urban": structures + ornamental vegetation

# 16 neighbours: 8 adjacent + 8 knight moves (reduces grid-shaped fires).
OFFSETS = np.array([
    (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1),
    (-2, 1), (-1, 2), (1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1),
], dtype=np.int64)


def fuel_index(fbfm):
    """Map FBFM40 codes to rows of the per-hour fuel table; -1 = won't burn."""
    codes = sorted(FUEL_MODELS) + [URBAN]
    lut = {c: i for i, c in enumerate(codes)}
    idx = np.full(fbfm.shape, -1, dtype=np.int64)
    for c, i in lut.items():
        idx[fbfm == c] = i
    return idx, codes


def moisture_series(wx, live_herb, live_woody):
    """Hourly dead fuel moisture via time-lag response to equilibrium moisture."""
    emc = fosberg_emc(wx["temperature_2m"].to_numpy(),  # °F
                      wx["relative_humidity_2m"].to_numpy())
    out = {}
    for name, tau in (("m1", 1.0), ("m10", 10.0), ("m100", 100.0)):
        m = np.empty_like(emc)
        m[0] = emc[0]
        k = 1 - math.exp(-1 / tau)
        for i in range(1, len(emc)):
            m[i] = m[i - 1] + k * (emc[i] - m[i - 1])
        out[name] = m
    out["m_herb"] = np.full_like(emc, live_herb)
    out["m_woody"] = np.full_like(emc, live_woody)
    return pd.DataFrame(out, index=wx.index)


def hourly_fuel_table(codes, moist, urban_proxy=102):
    """(hours, n_fuels, 7) array of Rothermel coefficients."""
    tab = np.zeros((len(moist), len(codes), 7))
    for h, row in enumerate(moist.itertuples()):
        args = (row.m1, row.m10, row.m100, row.m_herb, row.m_woody)
        for j, c in enumerate(codes):
            b = fuel_bed(urban_proxy if c == URBAN else c, *args)
            if b:
                tab[h, j] = (b["r0"], b["ir"], b["C"], b["B"], b["E"], b["ratio"], b["beta"])
    return tab


@numba.njit(cache=True)
def _ros(i, theta, h, fidx, tan2, upslope, waf, tab, wind_mph, wind_to, adjust, urban_row, urban_factor, spot):
    """Spread rate (m/min) from cell i in compass direction theta (rad)."""
    f = fidx[i]
    if f < 0:
        return 0.0
    r0, ir, C, B, E, ratio, beta = tab[h, f]
    if r0 <= 0:
        return 0.0
    u = min(wind_mph[h] * 88.0 * waf[i], 0.9 * ir)  # midflame ft/min, wind limit
    phi_w = C * u ** B * ratio ** (-E) if u > 0 else 0.0
    phi_s = 5.275 * beta ** (-0.3) * tan2[i]
    # Vector sum of wind and slope effects -> direction of max spread.
    vx = phi_w * math.sin(wind_to[h]) + phi_s * math.sin(upslope[i])
    vy = phi_w * math.cos(wind_to[h]) + phi_s * math.cos(upslope[i])
    phi_e = math.sqrt(vx * vx + vy * vy)
    rmax = r0 * (1.0 + phi_e)
    # Effective wind speed -> length/breadth ratio (Anderson 1983).
    ue = (phi_e * ratio ** E / C) ** (1.0 / B) / 88.0 if phi_e > 0 else 0.0
    lb = min(8.0, 0.936 * math.exp(0.2566 * ue) + 0.461 * math.exp(-0.1548 * ue) - 0.397)
    lb = max(lb, 1.0)
    ecc = math.sqrt(lb * lb - 1.0) / lb
    dir_max = math.atan2(vx, vy) if phi_e > 0 else 0.0
    r = rmax * (1.0 - ecc) / (1.0 - ecc * math.cos(theta - dir_max))
    if f == urban_row:
        r *= urban_factor
    # Ember spotting proxy: in strong wind, embers start fires downwind,
    # so the effective downwind spread rate grows with wind speed.
    if spot > 0 and wind_mph[h] > 15.0:
        downwind = math.cos(theta - wind_to[h])
        if downwind > 0:
            r *= 1.0 + spot * (wind_mph[h] - 15.0) / 15.0 * downwind
    return r * adjust / 3.28084


@numba.njit(cache=True)
def _arrival(H, W, start, fidx, tan2, upslope, waf, tab, wind_mph, wind_to,
             res, t_max, adjust, urban_row, urban_factor, hold_ros, spot, offsets):
    n = H * W
    t = np.full(n, np.inf)
    done = np.zeros(n, dtype=np.bool_)
    t[start] = 0.0
    heap = [(0.0, start)]
    nh = tab.shape[0]
    while heap:
        tu, u = heapq.heappop(heap)
        if done[u] or tu > t_max:
            continue
        done[u] = True
        h = min(int(tu // 60.0), nh - 1)
        ur, uc = u // W, u % W
        for k in range(offsets.shape[0]):
            vr, vc = ur + offsets[k, 0], uc + offsets[k, 1]
            if vr < 0 or vr >= H or vc < 0 or vc >= W:
                continue
            v = vr * W + vc
            if done[v] or fidx[v] < 0:
                continue
            dx, dy = offsets[k, 1] * res, -offsets[k, 0] * res
            theta = math.atan2(dx, dy)
            ru = _ros(u, theta, h, fidx, tan2, upslope, waf, tab, wind_mph, wind_to, adjust, urban_row, urban_factor, spot)
            rv = _ros(v, theta, h, fidx, tan2, upslope, waf, tab, wind_mph, wind_to, adjust, urban_row, urban_factor, spot)
            if ru <= 0 or rv <= 0:
                continue
            # Suppression proxy: after initial attack, crews hold slow edges.
            if tu > 60.0 and ru < hold_ros:
                continue
            dist = math.sqrt(dx * dx + dy * dy)
            tv = tu + 0.5 * dist * (1.0 / ru + 1.0 / rv)
            if tv < t[v]:
                t[v] = tv
                heapq.heappush(heap, (tv, v))
    return t


def wind_adjustment(fbfm, cc):
    """Midflame / 20-ft wind ratio: unsheltered surface fuels vs under canopy."""
    waf = np.full(fbfm.shape, 0.4)
    waf[np.isin(fbfm, list(range(141, 150)))] = 0.5  # deep shrub beds
    waf[cc >= 20] = 0.25
    waf[cc >= 40] = 0.15
    return waf


def simulate(dem, fbfm, cc, res, ignition_rc, wx, hours, spinup=0, live_herb=0.30, live_woody=0.60,
             adjust=1.0, urban_factor=0.3, hold_ros=0.0, gust_weight=0.5, spot=0.0):
    """Return arrival time in minutes (inf = unburned), hourly moisture and wind.

    `wx` is hourly weather starting `spinup` hours before ignition, so the
    slow 10-h and 100-h dead fuels have time to equilibrate.
    """
    H, W = dem.shape
    fidx, codes = fuel_index(fbfm)
    dzdy, dzdx = np.gradient(np.nan_to_num(dem), res)
    tan2 = (dzdx ** 2 + dzdy ** 2).ravel()
    upslope = np.arctan2(dzdx, -dzdy).ravel()  # compass bearing of steepest ascent
    waf = wind_adjustment(fbfm, cc).ravel()
    moist = moisture_series(wx, live_herb, live_woody).iloc[spinup: spinup + hours + 1]
    wx = wx.iloc[spinup: spinup + hours + 1]
    tab = hourly_fuel_table(codes, moist)
    # Reanalysis underestimates Santa Ana gusts; blend toward the gust speed.
    wind = ((1 - gust_weight) * wx["wind_speed_10m"] + gust_weight * wx["wind_gusts_10m"]).to_numpy()
    wind_20ft = wind / 1.15
    wind_to = np.radians((wx["wind_direction_10m"].to_numpy() + 180) % 360)
    r, c = ignition_rc
    # Start from the nearest burnable cell to the reported origin.
    if fidx[r, c] < 0:
        rr, cc_ = np.nonzero(fidx >= 0)
        k = np.argmin((rr - r) ** 2 + (cc_ - c) ** 2)
        r, c = rr[k], cc_[k]
    t = _arrival(H, W, int(r) * W + int(c), fidx.ravel(), tan2, upslope, waf, tab,
                 wind_20ft.astype(np.float64), wind_to.astype(np.float64),
                 float(res), float(hours * 60), float(adjust),
                 codes.index(URBAN), float(urban_factor), float(hold_ros), float(spot), OFFSETS)
    return t.reshape(H, W), moist, wind_20ft
