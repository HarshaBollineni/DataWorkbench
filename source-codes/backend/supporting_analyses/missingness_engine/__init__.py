"""DataWorkbench-owned port of the Missingness Atlas deterministic engine.

Ported from ``C:\\Src\\missingChecks\\backend\\src\\missing_checks`` at
version 0.2.0. HTTP, upload, background-job and frontend adapters were
deliberately excluded; this package contains only analytical domain logic.
"""

from .analysis import MissingnessAnalyzer
from .config import AnalysisConfig, analysis_parameter_catalog
from .models import MissingnessReport

__all__ = ["AnalysisConfig", "MissingnessAnalyzer", "MissingnessReport",
           "analysis_parameter_catalog"]
