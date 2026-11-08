"""Cron911: silent-gap + failure detection over jobs.json + executions.db.

D2 build shift implements the parser (reusing the parsing approach from
~/.hermes/scripts/cron-health-check.py).
"""
from __future__ import annotations
