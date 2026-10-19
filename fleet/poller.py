"""Fleet poller: run each adapter on its own interval and write telemetry.db.

Run as its own container: ``python -m fleet.poller``. A failing source backs off
(interval x 2^failures, capped at 8x) and keeps its last good data in the database,
where the view marks it stale. One source failing never stops the others.
"""

import logging
import time

from fleet import store
from fleet.adapters import all_adapters

logger = logging.getLogger("fleet.poller")

TICK_S = 5
PURGE_EVERY_S = 6 * 3600


def run_adapter(adapter, failures: int) -> int:
    """Poll one adapter and record the outcome. Returns the new failure count."""
    if not adapter.is_configured():
        store.record_source(adapter.name, configured=False, ok=None)
        return 0
    try:
        result = adapter.poll()
        store.upsert_machines(result.get("machines", []))
        for key, data in (result.get("accounts") or {}).items():
            store.upsert_account(key, adapter.name, data)
        store.record_source(adapter.name, configured=True, ok=True)
        return 0
    except Exception as e:
        logger.warning("%s poll failed: %s", adapter.name, e)
        store.record_source(adapter.name, configured=True, ok=False, error=str(e))
        return failures + 1


def run_once(adapters=None):
    """Poll every adapter once (used by tests and for a manual refresh)."""
    store.init_db()
    for adapter in adapters or all_adapters():
        run_adapter(adapter, 0)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    store.init_db()
    adapters = all_adapters()
    next_run = {a.name: 0.0 for a in adapters}
    failures = {a.name: 0 for a in adapters}
    next_purge = time.monotonic() + PURGE_EVERY_S
    logger.info("Fleet poller started with sources: %s", ", ".join(a.name for a in adapters))

    while True:
        now = time.monotonic()
        for adapter in adapters:
            if now < next_run[adapter.name]:
                continue
            failures[adapter.name] = run_adapter(adapter, failures[adapter.name])
            backoff = min(2 ** failures[adapter.name], 8)
            next_run[adapter.name] = time.monotonic() + adapter.interval_s * backoff
        if now >= next_purge:
            try:
                store.purge_samples()
            except Exception as e:
                logger.warning("Sample purge failed: %s", e)
            next_purge = now + PURGE_EVERY_S
        time.sleep(TICK_S)


if __name__ == "__main__":
    main()
