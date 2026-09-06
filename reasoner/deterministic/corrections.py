"""Select one revision per identifiable disclosure before Fact extraction."""
from __future__ import annotations

def select_fact_documents(documents, mode):
    if mode == "include_chain":
        return list(documents)
    def metadata(doc):
        return {**doc.raw, **doc.metadata}
    def corrected(doc):
        raw = metadata(doc).get("is_correction")
        return str(raw).lower() in {"true", "1"} or "정정" in str(metadata(doc).get("report_nm", ""))
    if mode == "original_only":
        return [doc for doc in documents if not corrected(doc)]
    def receipt(doc):
        meta = metadata(doc)
        return str(meta.get("rcept_no") or meta.get("doc_id") or doc.id)
    parents = {}
    for doc in documents:
        meta = metadata(doc)
        parent = next((meta.get(key) for key in ("original_rcept_no", "origin_rcept_no", "parent_rcept_no", "original_doc_id") if meta.get(key)), None)
        if parent:
            parents[receipt(doc)] = str(parent)
    def root(identifier):
        seen = set()
        while identifier in parents and identifier not in seen:
            seen.add(identifier)
            identifier = parents[identifier]
        return identifier
    def group(doc):
        meta = metadata(doc)
        company = str(meta.get("corp_name", ""))
        if company and meta.get("doc_group") == "periodic" and meta.get("base_year") and meta.get("base_month"):
            return (company, "periodic", str(meta["base_year"]), str(meta["base_month"]))
        # Same-titled contracts are not necessarily the same event.
        return (company, "receipt", root(receipt(doc)))
    latest = {}
    corrected_groups = {group(doc) for doc in documents if corrected(doc)}
    for doc in documents:
        key = group(doc)
        order = (str(metadata(doc).get("rcept_dt", "")), receipt(doc))
        if key not in latest or order > latest[key][0]:
            latest[key] = (order, receipt(doc))
    return [doc for doc in documents if group(doc) not in corrected_groups or receipt(doc) == latest[group(doc)][1]]
