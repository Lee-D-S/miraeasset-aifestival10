# Fallback and alternative references

When the requested company, period, and disclosure type cannot be matched, both RAG implementations return an explanatory fallback instead of an uncited answer.

The fallback has two layers:

1. The reason the exact request could not be answered.
2. Optional reference candidates, clearly separated from the answer evidence.

Candidates are grouped as follows:

- Same company, different period
- Same period, similar company

These candidates are reference material only. They are not added to `retrieved_context`, are not used as evidence for the original question, and are labelled as mismatched references in the answer.

The candidate search uses the existing metadata filters and vector store. If no candidate is available, the response states that no related indexed reference was found.
