"""Discovery hook — triggers OSM tool registration when tools are loaded."""

import opengis_backend.osm  # noqa: F401 — side-effect: registers osm_call
