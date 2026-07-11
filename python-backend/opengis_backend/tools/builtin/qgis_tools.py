"""Discovery hook — triggers QGIS tool registration when tools are loaded."""

import opengis_backend.integrations.qgis  # noqa: F401 — side-effect: registers all qgis tools
