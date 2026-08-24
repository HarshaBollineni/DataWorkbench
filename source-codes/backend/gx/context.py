"""GX v1.x in-memory context. Stateless/ephemeral — no filesystem project."""
from __future__ import annotations

import great_expectations as gx


def get_gx_context():
    """Return a fresh ephemeral DataContext.

    Importing the custom_expectations package registers every custom
    BatchExpectation subclass with GX's registry as a side effect.
    """
    from . import custom_expectations  # noqa: F401  (registers custom expectations)

    return gx.get_context(mode="ephemeral")
