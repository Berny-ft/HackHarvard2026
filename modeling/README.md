# Wildfire modelling — LA + Ventura counties

Data and models behind the FireTrace-style map. Everything uses public data
with no API keys.

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m wildfire all      # or: fetch | risk | patterns | spread | live
.venv/bin/python -m wildfire infra    # once: OSM power lines (slow, cached)
```

Units are US throughout: °F, mph, acres (`wildfire/units.py` converts).
Optional keys go in `modeling/.env`:

```
FIRMS_MAP_KEY=...        # NASA FIRMS satellite hot spots
NWS_CONTACT=...          # contact string for the api.weather.gov User-Agent
```

| Step | What it does | Main outputs in `out/` |
|---|---|---|
| `fetch` | CAL FIRE FRAP perimeters (1878–2025), CAL FIRE damage inspections (Eaton, Palisades), USGS 3DEP elevation, LANDFIRE 2024 fuels (FBFM40) + canopy cover, Open-Meteo daily weather 2000–2025 | `data/` |
| `risk` | 10-year burn-likelihood model (gradient boosting on terrain, fuel, fire history), trained 2005–14, tested 2015–25 | `burn_probability.{tif,png,json}`, `times_burned.*`, `risk_model_metrics.json` |
| `patterns` | Yearly/monthly trends, causes, Santa Ana analysis, burn rate by fuel/slope/elevation | `patterns.json`, `fire_history.geojson` |
| `live` | Open-Meteo 48 h forecast grid (Santa Ana tier, 1-h fuel moisture), NWS Red Flag Warnings with zone polygons, air quality, NASA FIRMS hot spots, NIFC current perimeters + incidents | `live/*.json`, `live/status.json` |
| `spread` | Rothermel fire-spread simulation of real incidents, scored against the real perimeter | `incidents/<fire>/…` |

## Spread model

- Rate of spread: Rothermel (1972) with Albini (1976) live-fuel moisture of
  extinction, Scott & Burgan 40 fuel models (`wildfire/rothermel.py`).
- Wind + slope combined as vectors; elliptical fire shape from effective
  wind speed (Anderson 1983).
- Dead fuel moisture from hourly temperature/humidity (Simard EMC + 1/10/100-h
  time lags with 5-day spin-up). Live moisture fixed per incident.
- Arrival time: time-dependent Dijkstra on a 30 m, 16-neighbour grid; the
  weather updates every hour.
- Ember spotting proxy: above 15 mph, downwind spread rate is boosted in
  proportion to wind speed (`spot`).
- Not modelled: explicit ember transport, crown fire, structure-to-structure
  ignition (urban cells use a damped grass proxy), real suppression lines.

Calibration grid-searches 5 parameters (ROS multiplier, urban factor,
suppression threshold, gust weight, spotting) on both 2025 fires. The
objective weights equally:
- **shape**: Sørensen overlap with the CAL FIRE final perimeter;
- **speed**: hourly simulated area vs the area seen burning by NASA FIRMS
  satellites (VIIRS + MODIS archive, rescaled to the mapped scar), scored
  symmetrically in log space so too fast and too slow both cost.

Each incident also records its **leave-one-fire-out** scores in `incident.json`.

Per-incident outputs (`out/incidents/<key>/`):
`perimeters.geojson` (hourly isochrones with acres), `timeseries.json`
(acres, growth, spread rate, wind, gusts, temp, RH, 1-h fuel moisture per hour),
`spread.png/json` (arrival-time overlay), `real_perimeter.geojson`,
`structures.geojson` (DINS damage + simulated arrival hour), `incident.json`.

Overlay `*.json` files hold Web-Mercator corner coordinates in
MapLibre/Mapbox image-source order (TL, TR, BR, BL).

Ignition points in `config.py` are approximate public origin locations.

## Santa Ana tiers

Defined once in `wildfire/fireweather.py` and used by both the history stats
and live alerts. Wind from 340–110°, plus:

| Tier | Gusts | Min RH | Days/yr | Large-fire acres | Lift |
|---|---|---|---|---|---|
| Santa Ana | ≥ 25 mph | ≤ 20% | 16 | 31% | 6× |
| Extreme | ≥ 40 mph | ≤ 15% | 1.8 | 23% | 24× |
