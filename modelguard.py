"""Model Guard: detect dead/free-gone/rate-limited models; recommend fallbacks.

v1 = detect + recommend only (never auto-swap). D2 build shift fills this in.
"""
from __future__ import annotations
