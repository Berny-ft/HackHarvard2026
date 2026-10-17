"""python -m wildfire [fetch|risk|patterns|spread|showcase|buildings|live|infra|all]

spread    calibrates on the train fires, then simulates the showcase fires
showcase  only the showcase fires, with the train fires' saved parameters
buildings pre-fire OSM buildings around each simulated fire, for the 3D replay
"""
import sys

from . import buildings, fetch, incidents, live, patterns, risk_model

STEPS = {"fetch": fetch.run, "risk": risk_model.run, "patterns": patterns.run, "spread": incidents.run, "showcase": incidents.run_showcase,
         "buildings": buildings.run,
         "live": live.run, "infra": lambda: live.run(include_osm=True)}

if __name__ == "__main__":
    args = sys.argv[1:] or ["all"]
    for step in ["fetch", "risk", "patterns", "spread", "live"] if args == ["all"] else args:
        STEPS[step]()
