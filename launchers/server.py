from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_file_logging(data_root: Path) -> None:
    log_dir = data_root / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "wendao-v3.log",
            maxBytes=1_000_000,
            backupCount=2,
            encoding="utf-8",
        )
    except OSError:
        return
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_logger.handlers = [handler]
    uvicorn_logger.setLevel(logging.WARNING)
    uvicorn_logger.propagate = False
    for name in ("uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.setLevel(logging.WARNING)
        logger.propagate = True


def run_server(
    application: object,
    *,
    host: str,
    port: int,
    data_root: Path,
    proxy_headers: bool = False,
) -> None:
    configure_file_logging(data_root)
    uvicorn.run(
        application,
        host=host,
        port=port,
        reload=False,
        log_level="warning",
        log_config=None,
        access_log=False,
        proxy_headers=proxy_headers,
        forwarded_allow_ips="127.0.0.1" if proxy_headers else None,
    )
