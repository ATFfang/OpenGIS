"""Discovery hook — triggers datasource tool registration when tools are loaded."""

import opengis_backend.integrations.datasource  # noqa: F401 — side-effect: registers datasource_call
