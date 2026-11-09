"""Application service for semantic and schema grounding context."""
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor


_RETRIEVAL_EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix="query-grounding")


def _append_unique(target: list[str], value, *, max_terms: int) -> None:
    text = str(value or "").strip()
    if text and len(text) <= 80 and text not in target and len(target) < max_terms:
        target.append(text)


def build_controlled_expansion(semantics: list[dict], field_values: list[dict], *, max_terms: int = 20) -> dict:
    terms: list[str] = []
    for item in semantics:
        _append_unique(terms, item.get("name"), max_terms=max_terms)
        definition = item.get("definition", {}) if isinstance(item, dict) else {}
        aliases = definition.get("aliases", []) if isinstance(definition, dict) else []
        if isinstance(aliases, list):
            for alias in aliases:
                _append_unique(terms, alias, max_terms=max_terms)
    for item in field_values:
        _append_unique(terms, item.get("value"), max_terms=max_terms)
        _append_unique(terms, item.get("matched_text"), max_terms=max_terms)
    return {"terms": terms, "evidence_count": len(semantics) + len(field_values), "mode": "curated"}


class QueryContextService:
    def __init__(self, *, load_catalog: Callable[[str], dict], retrieve: Callable[[str, dict, int], dict], catalog_path: str, semantic_search=None, value_search=None):
        self._load_catalog = load_catalog
        self._retrieve = retrieve
        self._catalog_path = catalog_path
        self._semantic_search = semantic_search
        self._value_search = value_search

    def retrieve(self, question: str, limit: int = 3) -> dict:
        catalog = self._load_catalog(self._catalog_path)
        if not self._semantic_search and not self._value_search:
            return self._retrieve(question, catalog, limit)
        base_future = _RETRIEVAL_EXECUTOR.submit(self._retrieve, question, catalog, limit)
        semantic_future = _RETRIEVAL_EXECUTOR.submit(self._semantic_search, question, limit) if self._semantic_search else None
        value_future = _RETRIEVAL_EXECUTOR.submit(self._value_search, question, limit) if self._value_search else None
        result = base_future.result()
        semantics = semantic_future.result() if semantic_future else []
        field_values = value_future.result() if value_future else []
        return {
            **result,
            "semantics": semantics,
            "field_values": field_values,
            "query_expansion": build_controlled_expansion(semantics, field_values),
        }
