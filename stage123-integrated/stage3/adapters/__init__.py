"""Adapters for upstream Stage1 and Stage2 contracts."""

from stage3.adapters.stage1 import adapt_stage1_intent
from stage3.adapters.stage2 import adapt_stage2_bundle, adapt_stage2_document

__all__ = ["adapt_stage1_intent", "adapt_stage2_bundle", "adapt_stage2_document"]
