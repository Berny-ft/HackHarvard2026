"""Hazard-safe routing shared by FireTrace and SurgeTrace: A -> B safe routes, Escape routes, directions.

  from routing import safe_route, escape_route, subscriber_route
  python -m sentinel route 34.20 -118.13 34.05 -118.25 --mode replay:eaton_2025 --depart 6
"""
from .api import escape_route, safe_route, subscriber_route  # noqa: F401
