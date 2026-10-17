"""Paths and thresholds for the storm-surge sentinel. LLM, email and webhook
settings are shared with the wildfire sentinel (sentinel/config.py)."""
import os

from sentinel.config import LEVELS, MODEL_OUT  # noqa: F401  (also loads agent/.env)

SURGE_OUT = MODEL_OUT / "surge"
STORMS = SURGE_OUT / "storms"
LIVE = SURGE_OUT / "live"

# Water this deep at the address counts as flooding (matches modeling/surge/config.py).
FLOOD_DEPTH_FT = 0.5
# How far ahead the sentinel looks with each official forecast.
HORIZON_H = 48

# High scenario: the forecast surge scaled up, standing in for NHC's 10%-exceedance
# P-Surge (reasonable worst case). Official forecasts miss landfall by tens of
# kilometres at two days out, and a 50 km shift can double the surge at a bay.
HIGH_SURGE_FACTOR = float(os.environ.get("SURGE_HIGH_FACTOR", "1.6"))
HIGH_SURGE_PAD_FT = float(os.environ.get("SURGE_HIGH_PAD_FT", "0.5"))

# Rule policy, fitted on the train storms only (evals/surge/calibrate.py):
#   evacuate: the expected forecast floods the address within 24 h, or it is flooding now.
#   warn:     the expected forecast floods the address within 36 h.
#   watch:    a tropical storm or hurricane forecast to pass within 300 km in 72 h,
#             for an address low enough to ever flood (spill level under 20 ft).
# Deciding on the high scenario instead evacuated far more low-but-dry homes on
# Sandy and Matthew for no gain in on-time evacuations, so the rules use the
# expected forecast and the high scenario is context for the LLM and the messages.
EVACUATE_H = float(os.environ.get("SURGE_EVAC_H", "24"))
WARN_H = float(os.environ.get("SURGE_WARN_H", "36"))
WATCH_KM, WATCH_H, WATCH_SPILL_FT = 300.0, 72.0, 20.0

# The "official zones" baseline: evacuation zones by elevation, ordered by the
# category forecast at closest approach (the way county zone maps work).
ZONE_SPILL_FT = {"A": 8.0, "B": 12.0, "C": 16.0}
ZONES_FOR_CATEGORY = {0: "", 1: "A", 2: "A", 3: "AB", 4: "ABC", 5: "ABC"}
