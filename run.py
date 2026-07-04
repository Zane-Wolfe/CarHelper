"""Container entrypoint.

Launches uvicorn honoring HOST/PORT from the environment, as PID 1 — so the
SIGTERM sent by `docker stop` / `docker compose down` reaches uvicorn directly
and it shuts down immediately (instead of a shell swallowing the signal and
forcing a 10s SIGKILL). `timeout_graceful_shutdown` keeps stop fast even when a
browser is holding the /ws/live WebSocket open.
"""
import logging

import uvicorn

from app import config
from app.logconfig import setup_logging

if __name__ == "__main__":
    # Configure our logging before uvicorn starts, and pass log_config=None so
    # uvicorn doesn't overwrite our formatter/handlers. Its loggers propagate to
    # our root handler (see logconfig.setup_logging).
    setup_logging(config.LOG_LEVEL)
    log = logging.getLogger("app.run")
    log.info("starting uvicorn on %s:%s (log level %s)", config.HOST, config.PORT, config.LOG_LEVEL)
    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        timeout_graceful_shutdown=3,
        log_config=None,
    )
