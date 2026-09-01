"""Public API entrypoint for the four-stage Agent engine."""

from integration.api import create_app
from integration.composition import build_pipeline


# Keep the FastAPI process importable even when deployment data is not mounted
# yet.  Corpus/DB/provider initialization happens at /ready or /answer time.
app = create_app(pipeline_factory=build_pipeline)
