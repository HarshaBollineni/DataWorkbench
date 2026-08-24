"""0.5.0 capture-only usage analytics.

The package intentionally contains no presentation surface.  The
event log is the durable measurement substrate; measures are pure readers of
that log and the catalogue consumes only the small per-asset summary it needs.
"""
