"""python -m surge [fetch|model|replays|live|roads|all]

model    calibrates the surge model on the train storms (scored on the showcase storms)
replays  builds every storm's replay files for the agent and the web map
roads    road graph per storm area, for flood-safe routing (agent/routing)
"""
import sys

from . import fetch, incidents, live, model

ROAD_PAD_DEG = 0.1      # beyond the flood-mapped box, so an escape can leave it


def roads(keys=None):
    """Every road class (streets too: the boxes are small) with bridge and tunnel tags, cached tiles.
    `python -m surge roads irma_2017 isabel_2003` builds just those storms."""
    from wildfire.live import ALL_CLASSES, DATA, fetch_road_graph

    from .config import STORMS
    for key, s in STORMS.items():
        if keys and key not in keys:
            continue
        w, so, e, n = s["bbox"]
        bbox = (w - ROAD_PAD_DEG, so - ROAD_PAD_DEG, e + ROAD_PAD_DEG, n + ROAD_PAD_DEG)
        g = fetch_road_graph(force=True, bbox=bbox, path=DATA / "surge_roads" / f"{key}.json", classes=ALL_CLASSES,
                             grid=(4, 4), tiles_dir=DATA / "surge_road_tiles" / key)
        print(f"[roads] {key}: {len(g['edges'])} edges, {sum(1 for x in g['edges'] if x.get('bridge'))} bridge, "
              f"{sum(1 for x in g['edges'] if x.get('tunnel'))} tunnel")


STEPS = {"fetch": fetch.run, "model": model.run, "replays": incidents.run, "live": live.run, "roads": roads}

if __name__ == "__main__":
    args = sys.argv[1:] or ["all"]
    if args[0] == "roads":
        roads(args[1:] or None)
        sys.exit()
    for step in ["fetch", "model", "replays", "live"] if args == ["all"] else args:
        STEPS[step]()
