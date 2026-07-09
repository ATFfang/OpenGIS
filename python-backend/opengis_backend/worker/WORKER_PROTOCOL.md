# OpenGIS Resident Worker Protocol

Resident workers are workspace-local Python services under `worker/<name>-<id>/`.
They are not one-off scripts.

## Package Shape

- `main.py`: the only process entrypoint. Keep it thin: load config, call datasource/service/publisher, sleep, repeat.
- `config.json`: runtime options such as polling interval, layer ids, API endpoints, and style settings.
- `src/datasource.py`: external I/O only. Fetch APIs, files, sockets, or streams here.
- `src/service.py`: business logic only. Validate, transform, aggregate, diff, and maintain state here.
- `src/publisher.py`: OpenGIS adapter only. Import `opengis_worker` here and emit UI/map events.
- `manifest.json`: service metadata and contract.
- `README.md`: worker-specific debugging notes.

## Dynamic Map Contract

Workers communicate by printing one JSON line to stdout. The manager forwards
only `rpc.ui.*` events to the frontend.

Required shape:

```json
{"opengis_method":"rpc.ui.map.dynamic_layer_update","params":{"layer_id":"live","mode":"full"}}
```

Use the generated `opengis_worker.py` helper instead of manually printing JSON:

- `emit_dynamic_layer_update(...)`: full GeoJSON frame.
- `emit_dynamic_layer_diff(...)`: diff frame with stable feature ids.
- `emit_dynamic_points(...)`: moving point features.
- `emit_dynamic_tracks(...)`: trajectory LineString features.
- `emit_moving_objects(...)`: synchronized points and tracks.

## Rules For Agents

- Do not edit `opengis_worker.py`; OpenGIS regenerates it.
- Do not create another entrypoint. The entrypoint is always `main.py`.
- When debugging, read worker logs and modify the smallest failing layer:
  datasource bugs go in `src/datasource.py`, transformation bugs go in
  `src/service.py`, rendering/output bugs go in `src/publisher.py`.
- After restarting a worker, verify health and recent logs before claiming it
  works.
- For high-frequency updates, emit one full frame first, then diff frames with
  increasing `sequence` values and stable feature ids.
