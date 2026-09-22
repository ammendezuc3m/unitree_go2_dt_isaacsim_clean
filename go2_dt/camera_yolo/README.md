# Camera YOLO

Writes `camera_yolo/outputs/live_camera_state.json`. See [`../../docs/json_contracts.md`](../../docs/json_contracts.md).

The standalone launcher is path-independent and uses the repository-level Python environment:

```bash
# From the repository root
bash requirements/setup_host_env.sh
./go2_dt/camera_yolo/run.sh
```

`run.sh` derives the repository root from its own location and uses `<repo-root>/.venv/bin/python`; no per-folder YOLO virtual environment is required.
