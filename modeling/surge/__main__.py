"""python -m surge [fetch|model|replays|live|all]

model    calibrates the surge model on the train storms (scored on the showcase storms)
replays  builds every storm's replay files for the agent and the web map
"""
import sys

from . import fetch, incidents, live, model

STEPS = {"fetch": fetch.run, "model": model.run, "replays": incidents.run, "live": live.run}

if __name__ == "__main__":
    args = sys.argv[1:] or ["all"]
    for step in ["fetch", "model", "replays", "live"] if args == ["all"] else args:
        STEPS[step]()
