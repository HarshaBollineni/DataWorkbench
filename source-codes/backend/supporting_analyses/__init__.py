"""Built-in supporting analyses owned by DataWorkbench."""

from analysis_runtime.capabilities import get_capability, register_capability
from .missingness import MissingnessCapability


def ensure_registered() -> None:
    try:
        get_capability(MissingnessCapability.capability_id)
    except KeyError:
        register_capability(MissingnessCapability())


ensure_registered()

__all__ = ["MissingnessCapability", "ensure_registered"]
