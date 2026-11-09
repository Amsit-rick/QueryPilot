"""Permission-scoped schema retrieval and deterministic join-path planning."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import math
import re
from typing import Any, Callable, Iterable


_ASCII_TOKEN = re.compile(r"[a-z0-9]+")
_CJK_TOKEN = re.compile(r"[\u3400-\u9fff]+")
EmbeddingScorer = Callable[[str, dict[str, str]], dict[str, float]]


@dataclass(frozen=True)
class SchemaRetrievalResult:
    schema: dict[str, list[str]]
    relationships: list[dict[str, Any]]
    selected_tables: tuple[str, ...]
    bridge_tables: tuple[str, ...] = ()
    join_paths: tuple[dict[str, Any], ...] = ()
    ambiguous_paths: tuple[dict[str, Any], ...] = ()
    retrieval_stats: dict[str, Any] = field(default_factory=dict)

    def context_summary(self) -> dict[str, Any]:
        return {
            "selected_tables": list(self.selected_tables),
            "bridge_tables": list(self.bridge_tables),
            "join_paths": list(self.join_paths),
            "ambiguous_paths": list(self.ambiguous_paths),
            "stats": dict(self.retrieval_stats),
        }


def _terms(value: Any) -> set[str]:
    text = str(value or "").lower()
    terms = set(_ASCII_TOKEN.findall(text))
    for chunk in _CJK_TOKEN.findall(text):
        terms.add(chunk)
        terms.update(chunk[index : index + 2] for index in range(max(0, len(chunk) - 1)))
    return {term for term in terms if term}


def _tokens(value: Any) -> list[str]:
    text = str(value or "").lower()
    tokens = list(_ASCII_TOKEN.findall(text))
    for chunk in _CJK_TOKEN.findall(text):
        tokens.append(chunk)
        tokens.extend(chunk[index : index + 2] for index in range(max(0, len(chunk) - 1)))
    return [token for token in tokens if token]


def _table_documents(columns: dict[str, list[str]], metadata_index: dict[str, dict[str, Any]]) -> dict[str, str]:
    documents = {}
    for table, table_columns in columns.items():
        metadata = metadata_index.get(table.lower(), {})
        aliases = metadata.get("aliases", [])
        aliases = aliases if isinstance(aliases, list) else []
        parts = [
            table,
            *table_columns,
            *[str(alias) for alias in aliases],
            str(metadata.get("description", metadata.get("comment", ""))),
            str(metadata.get("business_domain", metadata.get("domain", ""))),
        ]
        documents[table] = " ".join(part for part in parts if part)
    return documents


def _bm25_scores(question: str, documents: dict[str, str], *, k1: float = 1.5, b: float = 0.75) -> dict[str, float]:
    query_terms = _tokens(question)
    tokenized = {table: _tokens(document) for table, document in documents.items()}
    if not query_terms or not tokenized:
        return {table: 0.0 for table in documents}
    lengths = {table: len(tokens) for table, tokens in tokenized.items()}
    average_length = sum(lengths.values()) / len(lengths) if lengths else 1.0
    document_count = len(tokenized)
    document_frequency = {
        term: sum(term in set(tokens) for tokens in tokenized.values())
        for term in set(query_terms)
    }
    scores = {}
    for table, tokens in tokenized.items():
        frequencies: dict[str, int] = defaultdict(int)
        for token in tokens:
            frequencies[token] += 1
        score = 0.0
        for term in query_terms:
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            df = document_frequency.get(term, 0)
            inverse_document_frequency = math.log(1 + (document_count - df + 0.5) / (df + 0.5))
            denominator = frequency + k1 * (1 - b + b * lengths[table] / max(average_length, 1.0))
            score += inverse_document_frequency * (frequency * (k1 + 1)) / denominator
        scores[table] = round(score, 8)
    return scores


def _positive_ranks(scores: dict[str, float]) -> dict[str, int]:
    ranked = sorted(
        ((float(score), table.lower(), table) for table, score in scores.items() if float(score) > 0),
        key=lambda item: (-item[0], item[1]),
    )
    return {table: index for index, (_score, _key, table) in enumerate(ranked, start=1)}


def _ranked_tables(scores: dict[str, float], limit: int) -> list[str]:
    return [
        table
        for _score, _key, table in sorted(
            ((float(score), table.lower(), table) for table, score in scores.items() if float(score) > 0),
            key=lambda item: (-item[0], item[1]),
        )[: max(1, limit)]
    ]


def _canonical_tables(schema: dict[str, Iterable[str]]) -> tuple[dict[str, str], dict[str, list[str]]]:
    names: dict[str, str] = {}
    columns: dict[str, list[str]] = {}
    for table, raw_columns in schema.items():
        name = str(table)
        key = name.lower()
        if key in names:
            continue
        names[key] = name
        columns[name] = list(dict.fromkeys(str(column) for column in raw_columns))
    return names, columns


def _semantic_seeds(semantics: Iterable[dict[str, Any]], names: dict[str, str]) -> tuple[set[str], dict[str, set[str]]]:
    tables: set[str] = set()
    columns: dict[str, set[str]] = defaultdict(set)
    for entry in semantics:
        definition = entry.get("definition", {}) if isinstance(entry, dict) else {}
        if not isinstance(definition, dict):
            continue
        referenced = definition.get("tables", [])
        if not isinstance(referenced, list):
            referenced = []
        resolved = [names[str(table).lower()] for table in referenced if str(table).lower() in names]
        tables.update(resolved)
        raw_columns = definition.get("columns", [])
        if not isinstance(raw_columns, list):
            continue
        for raw in raw_columns:
            value = str(raw)
            if "." in value:
                table_name, column = value.rsplit(".", 1)
                canonical = names.get(table_name.lower())
                if canonical:
                    columns[canonical].add(column.lower())
            elif len(resolved) == 1:
                columns[resolved[0]].add(value.lower())
    return tables, columns


def _field_value_seeds(field_values: Iterable[dict[str, Any]], names: dict[str, str]) -> tuple[set[str], dict[str, set[str]]]:
    tables: set[str] = set()
    columns: dict[str, set[str]] = defaultdict(set)
    for item in field_values:
        if not isinstance(item, dict):
            continue
        table = names.get(str(item.get("table", "")).lower())
        column = str(item.get("column", "")).strip().lower()
        if table and column:
            tables.add(table)
            columns[table].add(column)
    return tables, columns


def _metadata_index(details: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    raw_tables = (details or {}).get("tables", {})
    if not isinstance(raw_tables, dict):
        return {}
    return {
        str(name).lower(): metadata
        for name, metadata in raw_tables.items()
        if isinstance(metadata, dict)
    }


def _score_table(
    question_text: str,
    question_terms: set[str],
    table: str,
    columns: list[str],
    metadata: dict[str, Any],
    semantic_seed: bool,
    value_seed: bool,
) -> tuple[int, set[str], dict[str, int]]:
    table_terms = _terms(table)
    aliases = metadata.get("aliases", [])
    if not isinstance(aliases, list):
        aliases = []
    description = str(metadata.get("description", metadata.get("comment", "")))
    components = {
        "semantic": 1000 if semantic_seed else 0,
        "field_value": 1100 if value_seed else 0,
        "table_name": 0,
        "table_token": 0,
        "table_alias": 0,
        "table_description": 0,
        "column": 0,
        "business_domain": 0,
        "metadata_quality": 0,
        "historical_success": 0,
    }
    reasons = set()
    if semantic_seed:
        reasons.add("semantic")
    if value_seed:
        reasons.add("field_value")
    normalized_table = table.lower()
    if normalized_table and normalized_table in question_text:
        components["table_name"] += 240
        reasons.add("table_name")
    overlap = question_terms & table_terms
    if overlap:
        components["table_token"] += 90 * len(overlap)
        reasons.add("table_token")
    for alias in aliases:
        alias_text = str(alias).lower().strip()
        if alias_text and (alias_text in question_text or question_text in alias_text):
            components["table_alias"] += 180
            reasons.add("table_alias")
    description_overlap = question_terms & _terms(description)
    if description_overlap:
        components["table_description"] += 45 * len(description_overlap)
        reasons.add("table_description")
    column_hits = 0
    for column in columns:
        column_text = column.lower()
        if column_text and column_text in question_text:
            column_hits += 1
        elif question_terms & _terms(column_text):
            column_hits += 1
    if column_hits:
        components["column"] += min(column_hits, 4) * 25
        reasons.add("column")
    business_domain = metadata.get("business_domain", metadata.get("domain", ""))
    domain_overlap = question_terms & _terms(business_domain)
    if domain_overlap:
        components["business_domain"] = 120 * len(domain_overlap)
        reasons.add("business_domain")
    try:
        quality = max(0.0, min(1.0, float(metadata.get("quality_score", 0) or 0)))
    except (TypeError, ValueError):
        quality = 0.0
    if quality:
        components["metadata_quality"] = round(quality * 20)
        reasons.add("metadata_quality")
    try:
        historical = max(0, int(metadata.get("historical_success_count", 0) or 0))
    except (TypeError, ValueError):
        historical = 0
    if historical:
        components["historical_success"] = min(historical, 20) * 2
        reasons.add("historical_success")
    return sum(components.values()), reasons, components


def _relation_graph(relationships: Iterable[dict[str, Any]], names: dict[str, str]) -> tuple[dict[str, list[tuple[str, int]]], list[dict[str, Any]]]:
    graph: dict[str, list[tuple[str, int]]] = defaultdict(list)
    normalized: list[dict[str, Any]] = []
    for relation in relationships:
        if not isinstance(relation, dict):
            continue
        from_name = names.get(str(relation.get("from_table", "")).lower())
        to_name = names.get(str(relation.get("to_table", "")).lower())
        if not from_name or not to_name:
            continue
        item = {**relation, "from_table": from_name, "to_table": to_name}
        index = len(normalized)
        normalized.append(item)
        graph[from_name].append((to_name, index))
        graph[to_name].append((from_name, index))
    for table in graph:
        graph[table].sort(key=lambda edge: (edge[0].lower(), edge[1]))
    return graph, normalized


def _shortest_paths(graph: dict[str, list[tuple[str, int]]], start: str, target: str, max_hops: int, max_paths: int = 4) -> list[tuple[list[str], list[int]]]:
    if start == target:
        return [([start], [])]
    distances = {start: 0}
    predecessors: dict[str, list[tuple[str, int]]] = defaultdict(list)
    queue = deque([start])
    while queue:
        table = queue.popleft()
        distance = distances[table]
        if distance >= max_hops:
            continue
        for neighbor, relation_index in graph.get(table, []):
            next_distance = distance + 1
            if neighbor not in distances:
                distances[neighbor] = next_distance
                predecessors[neighbor].append((table, relation_index))
                queue.append(neighbor)
            elif distances[neighbor] == next_distance:
                predecessors[neighbor].append((table, relation_index))
    if target not in distances or distances[target] > max_hops:
        return []
    paths: list[tuple[list[str], list[int]]] = []

    def build(node: str, tables: list[str], edges: list[int]) -> None:
        if len(paths) >= max_paths:
            return
        if node == start:
            paths.append((list(reversed(tables + [start])), list(reversed(edges))))
            return
        for previous, relation_index in predecessors[node]:
            build(previous, tables + [node], edges + [relation_index])

    build(target, [], [])
    return paths


def _primary_keys(metadata_index: dict[str, dict[str, Any]], table: str) -> set[str]:
    metadata = metadata_index.get(table.lower(), {})
    values = metadata.get("primary_key", [])
    return {str(value).lower() for value in values} if isinstance(values, list) else set()


def retrieve_schema(
    question: str,
    schema: dict[str, Iterable[str]],
    *,
    relationships: Iterable[dict[str, Any]] = (),
    semantics: Iterable[dict[str, Any]] = (),
    field_values: Iterable[dict[str, Any]] = (),
    schema_details: dict[str, Any] | None = None,
    enabled: bool = True,
    table_threshold: int = 50,
    max_tables: int = 20,
    max_columns_per_table: int = 40,
    max_hops: int = 3,
    embedding_scorer: EmbeddingScorer | None = None,
) -> SchemaRetrievalResult:
    """Retrieve model context only from an already authorized schema."""
    names, columns = _canonical_tables(schema)
    metadata_index = _metadata_index(schema_details)
    semantic_tables, semantic_columns = _semantic_seeds(semantics, names)
    value_tables, value_columns = _field_value_seeds(field_values, names)
    for table, values in value_columns.items():
        semantic_columns[table].update(values)
    graph, normalized_relationships = _relation_graph(relationships, names)
    question_text = question.lower().strip()
    question_terms = _terms(question_text)
    documents = _table_documents(columns, metadata_index)
    bm25_scores = _bm25_scores(question, documents)
    embedding_scores: dict[str, float] = {}
    embedding_status = "disabled"
    embedding_error_code = ""
    if embedding_scorer is not None:
        try:
            raw_scores = embedding_scorer(question, dict(documents))
            if not isinstance(raw_scores, dict):
                raise ValueError("invalid embedding scores")
            for raw_table, raw_score in raw_scores.items():
                canonical = names.get(str(raw_table).lower())
                if not canonical:
                    continue
                numeric = float(raw_score)
                if not math.isfinite(numeric):
                    continue
                embedding_scores[canonical] = max(0.0, min(1.0, numeric))
            embedding_status = "available"
        except Exception:
            embedding_scores = {}
            embedding_status = "degraded"
            embedding_error_code = "E_EMBEDDING_UNAVAILABLE"

    base_scores: dict[str, int] = {}
    score_details: dict[str, tuple[set[str], dict[str, int]]] = {}
    for table, table_columns in columns.items():
        score, reasons, components = _score_table(
            question_text,
            question_terms,
            table,
            table_columns,
            metadata_index.get(table.lower(), {}),
            table in semantic_tables,
            table in value_tables,
        )
        base_scores[table] = score
        score_details[table] = (reasons, components)

    rank_maps = [_positive_ranks({table: float(score) for table, score in base_scores.items()}), _positive_ranks(bm25_scores)]
    if embedding_status == "available":
        rank_maps.append(_positive_ranks(embedding_scores))
    scored = []
    for table in columns:
        reasons, components = score_details[table]
        components["bm25"] = max(0, round(bm25_scores.get(table, 0.0) * 100))
        components["embedding"] = max(0, round(embedding_scores.get(table, 0.0) * 200))
        rrf = sum(1000 / (60 + ranks[table]) for ranks in rank_maps if table in ranks)
        components["reciprocal_rank_fusion"] = max(0, round(rrf))
        if components["bm25"]:
            reasons.add("bm25")
        if components["embedding"]:
            reasons.add("embedding")
        if components["reciprocal_rank_fusion"]:
            reasons.add("reciprocal_rank_fusion")
        score = sum(components.values())
        scored.append((score, table.lower(), table, reasons, components))
    scored.sort(key=lambda item: (-item[0], item[1]))
    use_retrieval = enabled and len(columns) > max(0, table_threshold)
    if not use_retrieval:
        seeds = [item[2] for item in scored]
    else:
        positive = [item[2] for item in scored if item[0] > 0]
        seeds = positive[: max(1, max_tables)]
        if not seeds and scored:
            seeds = [scored[0][2]]

    selected: list[str] = []
    bridges: set[str] = set()
    join_paths: list[dict[str, Any]] = []
    ambiguous_paths: list[dict[str, Any]] = []
    if seeds:
        selected.append(seeds[0])
    for seed in seeds[1:]:
        if seed in selected:
            continue
        paths = _shortest_paths(graph, selected[0], seed, max_hops) if use_retrieval else []
        path_payloads = []
        for path_tables, relation_indexes in paths:
            path_relationships = [normalized_relationships[index] for index in relation_indexes]
            confidence = min(
                (max(0.0, min(1.0, float(item.get("confidence", 0.5) or 0.5))) for item in path_relationships),
                default=1.0,
            )
            path_payloads.append({
                "tables": path_tables,
                "relationships": path_relationships,
                "confidence": round(confidence, 6),
            })
        path_payloads.sort(key=lambda item: (-item["confidence"], tuple(table.lower() for table in item["tables"])))
        chosen_tables = path_payloads[0]["tables"] if path_payloads else [seed]
        additions = [table for table in chosen_tables if table not in selected]
        if use_retrieval and len(selected) + len(additions) > max_tables:
            continue
        selected.extend(additions)
        if path_payloads:
            join_paths.append({"from_table": selected[0], "to_table": seed, **path_payloads[0]})
            bridges.update(chosen_tables[1:-1])
            if len(path_payloads) > 1:
                ambiguous_paths.append({
                    "from_table": selected[0],
                    "to_table": seed,
                    "alternatives": path_payloads,
                })

    selected_set = set(selected)
    bridges.difference_update(seeds)
    scoped_relationships = [
        relation for relation in normalized_relationships
        if relation["from_table"] in selected_set and relation["to_table"] in selected_set
    ]
    mandatory_columns: dict[str, set[str]] = defaultdict(set)
    for table in selected:
        mandatory_columns[table].update(_primary_keys(metadata_index, table))
        mandatory_columns[table].update(semantic_columns.get(table, set()))
    for relation in scoped_relationships:
        mandatory_columns[relation["from_table"]].update(str(value).lower() for value in relation.get("from_columns", []))
        mandatory_columns[relation["to_table"]].update(str(value).lower() for value in relation.get("to_columns", []))

    reduced_schema: dict[str, list[str]] = {}
    for table in selected:
        table_columns = columns[table]
        if not use_retrieval or len(table_columns) <= max_columns_per_table:
            reduced_schema[table] = table_columns
            continue
        mandatory = mandatory_columns[table]
        ranked_columns = sorted(
            table_columns,
            key=lambda column: (
                0 if column.lower() in mandatory else 1,
                0 if question_terms & _terms(column) else 1,
                column.lower(),
            ),
        )
        keep_count = max(max_columns_per_table, len(mandatory))
        reduced_schema[table] = ranked_columns[:keep_count]

    total_columns = sum(len(values) for values in columns.values())
    selected_columns = sum(len(values) for values in reduced_schema.values())
    score_summary = {
        item[2]: {"score": item[0], "reasons": sorted(item[3]), "components": dict(item[4])}
        for item in scored if item[2] in selected_set
    }
    stats = {
        "retrieval_applied": use_retrieval,
        "total_tables": len(columns),
        "selected_table_count": len(selected),
        "total_columns": total_columns,
        "selected_column_count": selected_columns,
        "schema_reduction_ratio": round(1 - (selected_columns / total_columns), 6) if total_columns else 0.0,
        "table_scores": score_summary,
        "rank_sources": [
            "heuristic",
            "bm25",
            *(["embedding"] if embedding_status == "available" else []),
            "relationship_graph",
        ],
        "embedding": {
            "status": embedding_status,
            "score_count": len(embedding_scores),
            **({"error_code": embedding_error_code} if embedding_error_code else {}),
        },
        "retrieval_mode": "hybrid" if embedding_status == "available" else "bm25_graph",
        "source_rankings": {
            "heuristic": _ranked_tables({table: float(score) for table, score in base_scores.items()}, max_tables),
            "bm25": _ranked_tables(bm25_scores, max_tables),
            **({"embedding": _ranked_tables(embedding_scores, max_tables)} if embedding_status == "available" else {}),
            "fused": [item[2] for item in scored if item[0] > 0][: max(1, max_tables)],
            "graph_augmented": list(selected)[: max(1, max_tables)],
        },
    }
    return SchemaRetrievalResult(
        schema=reduced_schema,
        relationships=scoped_relationships,
        selected_tables=tuple(selected),
        bridge_tables=tuple(sorted(bridges)),
        join_paths=tuple(join_paths),
        ambiguous_paths=tuple(ambiguous_paths),
        retrieval_stats=stats,
    )


def ambiguous_path_gate_reasons(sql: str, ambiguous_paths: Iterable[dict[str, Any]]) -> tuple[str, ...]:
    """Flag SQL that joins both endpoints of a non-unique shortest path."""
    if not ambiguous_paths:
        return ()
    try:
        import sqlglot
        from sqlglot import exp

        statement = sqlglot.parse_one(sql)
        tables = {str(table.name).lower() for table in statement.find_all(exp.Table)}
    except Exception:
        return ()
    for item in ambiguous_paths:
        endpoints = {str(item.get("from_table", "")).lower(), str(item.get("to_table", "")).lower()}
        if endpoints <= tables:
            return ("ambiguous_join_path",)
    return ()


__all__ = ["EmbeddingScorer", "SchemaRetrievalResult", "ambiguous_path_gate_reasons", "retrieve_schema"]
