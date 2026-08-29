"""Public API entrypoint for the four-stage Agent engine."""

from integration.api import create_app
from integration.composition import build_pipeline


# Stage implementations are injected here when the team integration is ready.
# Keeping the app importable now preserves the deployment entrypoint.
app = create_app(build_pipeline())
