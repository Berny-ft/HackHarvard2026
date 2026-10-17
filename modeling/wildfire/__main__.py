"""python -m wildfire [fetch|risk|patterns|spread|live|infra|all]"""
import sys

from . import fetch, incidents, live, patterns, risk_model

STEPS = {"fetch": fetch.run, "risk": risk_model.run, "patterns": patterns.run, "spread": incidents.run,
         "live": live.run, "infra": lambda: live.run(include_osm=True)}

if __name__ == "__main__":
    args = sys.argv[1:] or ["all"]
    for step in ["fetch", "risk", "patterns", "spread", "live"] if args == ["all"] else args:
        STEPS[step]()
