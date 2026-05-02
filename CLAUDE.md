# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Local development (no Docker):**
```bash
pip install -e ".[dev]"

# Run all three services as separate processes
cup-robot       # listens on :8001
cup-handineye   # listens on :8002
cup-handtoeye   # listens on :8003
```

**Docker (production-like):**
```bash
docker compose up --build          # all services
docker compose up --build robot    # single service
docker compose build               # rebuild images only
```

**Tests:**
```bash
pytest
pytest tests/path/to/test_file.py::test_name   # single test
```

## Architecture

### Two deployment modes

The codebase supports running as:
1. **Three separate microservices** (Docker Compose default): each entrypoint in `server/entrypoints/` creates its own FastAPI app with only its domain router, connects independently to rosbridge, and listens on its own port (8001–8003). Nginx routes requests across them.
2. **Single monolithic app** (`server/main.py`): `create_app()` registers all routers and shares one rosbridge connection. Used when running `cup-server` directly.

The entrypoints patch `AppSettings` at startup to read `ROSBRIDGE_HOST` / `ROSBRIDGE_PORT` env vars; `main.py` does not do this.

### Dependency wiring (not FastAPI `Depends`)

Routers hold a module-level domain variable (`robot_domain`, `handineye_domain`, etc.) set by a `set_*_domain()` function called from the lifespan context. Routers fail with HTTP 503 if the lifespan hasn't run. Do not use FastAPI `Depends` for domain access — the pattern is intentional.

### RosBridge singleton

`RosBridge` is a singleton (`RosBridge.get(config)`). It manages all ROS topic subscriptions in a shared dict keyed by topic name. Multiple subscribers on the same topic share one `roslibpy.Topic` object. Connection runs in a thread via `asyncio.run_in_executor`. Reset via `RosBridge.reset()` on shutdown.

### Data flow

```
ROS 2 topics
    → rosbridge WebSocket server (:9090)
        → roslibpy (RosBridge singleton)
            → domain callbacks (update in-memory state)
                → REST endpoints pull state on request
                → WebSocket endpoints poll/stream state to browser
```

Camera frames arrive as `sensor_msgs/msg/CompressedImage` (base64-encoded). `CameraStream._on_frame` decodes, transcodes to JPEG if not already, then signals waiting `asyncio.Event`s. WebSocket clients consume via `async for frame in stream.frames()`.

### LaunchManager

Tracks ROS 2 subprocesses started via `asyncio.create_subprocess_shell`. Valid commands are defined in `server/ros/launch.py` (`BRINGUP_COMMANDS`, `TASK_COMMANDS`). Only one task runs at a time. Bringup commands invoke shell scripts; task commands build `ros2 launch <pkg> <cmd>.launch.py` invocations. The workspace path is resolved relative to the `server/` package location in `config.py:_default_workspace`.

### Calibration storage

`.npy` files stored in `cup_stack/ros2/src/cup_stack/config/`. Returns `np.eye(4)` if the file doesn't exist. Mounted read-only into Docker containers via the `../cup_stack/ros2:/app/ros2:ro` volume.

### Config

All config lives in `server/config.py` as frozen dataclasses nested under `AppSettings`. No pydantic, no `.env` file — only the entrypoints manually read env vars and patch `AppSettings` before the lifespan runs.

### Nginx routing

| Path prefix | Upstream |
|---|---|
| `/api/robot/` | `robot:8001` |
| `/api/handineye/` | `handineye:8002` |
| `/api/handtoeye/` | `handtoeye:8003` |
| `/ws/robot/state`, `/ws/task/log` | `robot:8001` |
| `/ws/camera/handineye` | `handineye:8002` |
| `/ws/camera/handtoeye` | `handtoeye:8003` |
| `/` | Frontend SPA (built from `../frontend`) |
