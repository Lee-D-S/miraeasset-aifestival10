"""Run one retrieval experiment profile behind the normal answer contract."""

from integration.api import create_app
from retriever.experiment_service import build_pipeline


app = create_app(pipeline_factory=build_pipeline)

