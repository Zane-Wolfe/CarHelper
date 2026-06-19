"""Container entrypoint.

Launches uvicorn honoring HOST/PORT from the environment, as PID 1 — so the
SIGTERM sent by `docker stop` / `docker compose down` reaches uvicorn directly
and it shuts down immediately (instead of a shell swallowing the signal and
forcing a 10s SIGKILL). `timeout_graceful_shutdown` keeps stop fast even when a
browser is holding the /ws/live WebSocket open.
"""
import uvicorn

from app import config

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        timeout_graceful_shutdown=3,
    )
