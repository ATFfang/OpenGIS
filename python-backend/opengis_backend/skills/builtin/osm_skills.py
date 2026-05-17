"""Discovery hook — triggers OSM skill registration when skills are loaded."""

import opengis_backend.osm  # noqa: F401 — side-effect: registers osm_call
