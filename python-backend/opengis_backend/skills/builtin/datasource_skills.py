"""Discovery hook — triggers datasource skill registration when skills are loaded."""

import opengis_backend.datasource  # noqa: F401 — side-effect: registers datasource_call
