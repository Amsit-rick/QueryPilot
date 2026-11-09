import json
from pathlib import Path


def load_catalog(path: str = "./data/catalog.json") -> dict:
    file = Path(path)
    if not file.exists():
        return {"version": "0.1.0", "examples": [], "metrics": []}
    return json.loads(file.read_text(encoding="utf-8"))


def retrieve_context(question: str, catalog: dict, limit: int = 3) -> dict:
    terms = {term.lower() for term in question.split() if len(term) > 1}

    def score(item: dict) -> int:
        text = " ".join(str(value) for value in item.values()).lower()
        return sum(term in text for term in terms)

    examples = sorted(catalog.get("examples", []), key=score, reverse=True)[:limit]
    metrics = sorted(catalog.get("metrics", []), key=score, reverse=True)[:limit]
    return {"catalog_version": catalog.get("version", "unknown"), "examples": examples, "metrics": metrics}
