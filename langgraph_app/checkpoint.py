def build_memory_checkpointer():
    """Return an in-memory checkpointer for local development and tests."""
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()

