"""Discovery hook — triggers QGIS skill registration when skills are loaded."""

import opengis_backend.qgis  # noqa: F401 — side-effect: registers all qgis skills
