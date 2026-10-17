"""Paths, thresholds and model settings for the sentinel agent."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # agent/
REPO = ROOT.parent
MODEL_OUT = Path(os.environ.get("FIRETRACE_OUT", REPO / "modeling" / "out"))
INCIDENTS = MODEL_OUT / "incidents"
LIVE = MODEL_OUT / "live"
STATE = Path(os.environ.get("SENTINEL_STATE", ROOT / "state"))   # subscribers + alert log


def load_env(path=ROOT / ".env"):
    """Same minimal .env reader the modelling pipeline uses."""
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

LEVELS = ["none", "watch", "warn", "evacuate"]

# A fire seen at a point is treated as "active" for this long (hours). Satellite
# overpasses are hours apart, so a shorter window drops fires between passes.
ACTIVE_WINDOW_H = 12.0

# Head-fire rate of spread (km/h) per mph of 20-ft wind, before moisture damping.
# Fitted on Eaton only (see evals/calibrate.py); Palisades is held out.
ROS_KMH_PER_MPH = float(os.environ.get("SENTINEL_ROS_K", "0.03"))
# Cap on the fire ellipse's length-to-breadth ratio. Embers and local wind
# shifts widen real Santa Ana fires well beyond the textbook ellipse.
MAX_LENGTH_TO_BREADTH = float(os.environ.get("SENTINEL_MAX_LB", "8.0"))
# How many hours of unseen travel to credit a fire point with since it was
# observed. Full credit assumes every old detection is still a running front,
# which badly overpredicts; most of a fire's points are burned-out interior.
STALE_CREDIT_H = float(os.environ.get("SENTINEL_STALE_H", "0.0"))

# Rule policy thresholds: (max ETA hours, max distance km) per level. A level
# fires when either the downwind ETA or the plain distance ring says so. Fitting
# the evacuate ring on Eaton alone picks 2 km, which does worse on held-out
# Palisades (72% vs 76% evacuated in time); two fires cannot pin it down, so
# the wider, safer 3 km ring is the default.
EVACUATE_KM = float(os.environ.get("SENTINEL_EVAC_KM", "3.0"))
THRESHOLDS = {
    "evacuate": (3.0, EVACUATE_KM),
    "warn": (8.0, 8.0),
    "watch": (24.0, 15.0),
}

# LLM via the Chalk Model Gateway (OpenAI-compatible). Without a key, the
# agent falls back to the rule policy and templated messages.
CHALK_ROUTER_URL = os.environ.get("CHALK_ROUTER_URL", "https://api.chalk.ai/v1/router")
CHALK_ROUTER_API_KEY = os.environ.get("CHALK_ROUTER_API_KEY")
# The HackHarvard key belongs to Chalk's own environment, so the router env can
# differ from the environment the sandboxes and evaluations run in.
CHALK_ROUTER_ENV_ID = os.environ.get("CHALK_ROUTER_ENV_ID") or os.environ.get("CHALK_ENV_ID")
MODEL = os.environ.get("SENTINEL_MODEL", "openai/gpt-6.1-sol")
MAX_TOKENS = int(os.environ.get("SENTINEL_MAX_TOKENS", "2000"))   # reasoning models spend some before answering

WEBHOOK_SECRET = os.environ.get("SENTINEL_WEBHOOK_SECRET", "dev-secret")
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.environ.get("TWILIO_FROM")
