"""Standalone worker: fires durable timers.

Run one alongside the API when the API runs with LAABAM_INLINE_WORKER=0 (several API
instances, one worker). The atomic claim makes a second worker safe too.

  python -m laabam.worker
"""
from __future__ import annotations

import logging
import signal

from .app import build_services
from .config import settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    svc = build_services(settings, schema=False)

    def _stop(*_):
        svc.worker.stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    logging.getLogger("laabam.worker").info("worker started")
    svc.worker.loop()


if __name__ == "__main__":
    main()
