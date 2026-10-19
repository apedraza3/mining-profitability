"""Fleet monitor: what is running now, what it's making, and what it costs.

The poller (``python -m fleet.poller``) is the only process that calls devices and
vendor APIs and the only writer of ``data/telemetry.db``. The web app only reads it.
"""
