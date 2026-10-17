"""Shared fire-weather rules, used by both the history stats and live alerts.

Thresholds were picked on 2000-2025 data for LA + Ventura (104 fires over
1,000 acres, daily max gust / min RH / dominant wind direction):

  tier 1 "Santa Ana":         4.4% of days, 31% of large-fire acres, 6.3x lift
  tier 2 "Extreme Santa Ana": 0.5% of days, 23% of large-fire acres, 25x lift
"""
import numpy as np

from .rothermel import fosberg_emc

OFFSHORE_FROM_DEG = (340, 110)  # wind coming from N through E
TIERS = {  # level: (min gust mph, max RH %)
    1: (25, 20),
    2: (40, 15),
}
TIER_NAMES = {0: "None", 1: "Santa Ana", 2: "Extreme Santa Ana"}


def offshore(wind_dir_deg):
    lo, hi = OFFSHORE_FROM_DEG
    d = np.asarray(wind_dir_deg) % 360
    return (d >= lo) | (d <= hi)


def santa_ana_level(wind_dir_deg, gust_mph, rh_pct):
    """0 = none, 1 = Santa Ana, 2 = extreme. Works on scalars or arrays."""
    off = offshore(wind_dir_deg)
    level = np.zeros(np.shape(off), dtype=int)
    for lvl, (gust, rh) in TIERS.items():
        level = np.where(off & (np.asarray(gust_mph) >= gust) & (np.asarray(rh_pct) <= rh), lvl, level)
    return level


def dead_fuel_moisture_1h_pct(temp_f, rh_pct):
    """1-h dead fuel moisture (%), Simard EMC. Below ~5% is extreme."""
    return np.round(fosberg_emc(np.asarray(temp_f, float), np.asarray(rh_pct, float)) * 100, 1)
