"""Deterministic insights over already-authorized and redacted query rows."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from math import isfinite
from statistics import median
from typing import Any

from app.agent.contracts import AnalysisMode, DerivedAnalysis


_KEYWORDS = {
    AnalysisMode.DESCRIPTIVE_STATISTICS: ("进一步分析", "描述统计", "两步分析", "descriptive statistics"),
    AnalysisMode.TREND: ("趋势", "走势", "trend"),
    AnalysisMode.PERIOD_COMPARISON: ("同比", "环比", "period comparison", "year over year", "month over month"),
    AnalysisMode.ANOMALY_DETECTION: ("异常值", "异常", "离群", "anomaly", "outlier"),
    AnalysisMode.AUTO_INSIGHTS: ("智能分析", "自动洞察", "综合洞察", "auto insights"),
}


def infer_analysis_mode(question: str) -> AnalysisMode:
    """Map explicit analysis language to a bounded deterministic mode."""
    value = question.casefold()
    matches = [mode for mode, terms in _KEYWORDS.items() if any(term in value for term in terms)]
    if AnalysisMode.AUTO_INSIGHTS in matches or len(matches) > 1:
        return AnalysisMode.AUTO_INSIGHTS
    return matches[0] if matches else AnalysisMode.NONE


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        result = float(value)
    except (InvalidOperation, OverflowError, TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        for pattern in ("%Y-%m", "%Y"):
            try:
                return datetime.strptime(text, pattern)
            except ValueError:
                continue
    return None


def _rounded(value: float) -> float:
    return round(value, 6)


def _change(current: float, baseline: float) -> tuple[float, float | None]:
    absolute = _rounded(current - baseline)
    percent = None if baseline == 0 else _rounded(absolute / abs(baseline) * 100)
    return absolute, percent


def _numeric_metrics(columns: list[str], rows: list[list[Any]]) -> dict[str, dict[str, float | int]]:
    metrics: dict[str, dict[str, float | int]] = {}
    for index, column in enumerate(columns):
        numbers = [number for row in rows if index < len(row) if (number := _number(row[index])) is not None]
        if numbers:
            metrics[column] = {
                "count": len(numbers),
                "min": _rounded(min(numbers)),
                "max": _rounded(max(numbers)),
                "average": _rounded(sum(numbers) / len(numbers)),
            }
    return metrics


def _time_index(columns: list[str], rows: list[list[Any]]) -> int | None:
    candidates: list[tuple[int, int]] = []
    for index, _column in enumerate(columns):
        parsed = sum(1 for row in rows if index < len(row) and _instant(row[index]) is not None)
        if parsed >= 2:
            candidates.append((parsed, index))
    return max(candidates, default=(0, -1))[1] if candidates else None


def _series(columns: list[str], rows: list[list[Any]], time_index: int) -> tuple[dict[str, list[tuple[datetime, str, float]]], list[str]]:
    result: dict[str, list[tuple[datetime, str, float]]] = {}
    limitations: list[str] = []
    for value_index, column in enumerate(columns):
        if value_index == time_index:
            continue
        points = []
        for row in rows:
            if max(time_index, value_index) >= len(row):
                continue
            instant = _instant(row[time_index])
            number = _number(row[value_index])
            if instant is not None and number is not None:
                points.append((instant, str(row[time_index]), number))
        points.sort(key=lambda item: item[0])
        if len(points) < 2:
            continue
        instants = [point[0] for point in points]
        if len(set(instants)) != len(instants):
            limitations.append(f"{column} 在同一时间点存在多行，未自动聚合或生成时序洞察。")
            continue
        result[column] = points
    return result, limitations


def _trend(column: str, time_column: str, points: list[tuple[datetime, str, float]]) -> dict:
    first, last = points[0], points[-1]
    absolute, percent = _change(last[2], first[2])
    return {
        "column": column,
        "time_column": time_column,
        "start_period": first[1],
        "end_period": last[1],
        "start_value": _rounded(first[2]),
        "end_value": _rounded(last[2]),
        "absolute_change": absolute,
        "percent_change": percent,
        "direction": "up" if absolute > 0 else "down" if absolute < 0 else "flat",
        "points": len(points),
    }


def _comparison(kind: str, column: str, current, baseline) -> dict:
    absolute, percent = _change(current[2], baseline[2])
    return {
        "kind": kind,
        "column": column,
        "current_period": current[1],
        "baseline_period": baseline[1],
        "current_value": _rounded(current[2]),
        "baseline_value": _rounded(baseline[2]),
        "absolute_change": absolute,
        "percent_change": percent,
    }


def _comparisons(column: str, points: list[tuple[datetime, str, float]]) -> list[dict]:
    current = points[-1]
    result = [_comparison("previous_period", column, current, points[-2])]
    previous_year = next(
        (
            point
            for point in reversed(points[:-1])
            if point[0].year == current[0].year - 1
            and point[0].month == current[0].month
            and point[0].day == current[0].day
        ),
        None,
    )
    if previous_year:
        result.append(_comparison("year_over_year", column, current, previous_year))
    return result


def _anomalies(column: str, points: list[tuple[datetime, str, float]]) -> list[dict]:
    if len(points) < 4:
        return []
    values = [point[2] for point in points]
    center = median(values)
    deviations = [abs(value - center) for value in values]
    mad = median(deviations)
    scale = mad / 0.6745 if mad else max(abs(center) * 0.5, 1e-9)
    threshold = 3.5 if mad else 1.0
    result = []
    for point, deviation in zip(points, deviations):
        score = deviation / scale
        if score > threshold:
            result.append(
                {
                    "column": column,
                    "period": point[1],
                    "value": _rounded(point[2]),
                    "baseline": _rounded(center),
                    "score": _rounded(score),
                    "method": "mad" if mad else "median_deviation",
                }
            )
    return sorted(result, key=lambda item: item["score"], reverse=True)[:20]


def describe_result(output, analysis_plan: dict) -> DerivedAnalysis:
    columns, rows, truncated = output
    mode = AnalysisMode(analysis_plan["analysis_mode"])
    metrics = _numeric_metrics(columns, rows)
    limitations = ["查询结果已截断，洞察仅基于当前返回行。"] if truncated else []
    if mode == AnalysisMode.DESCRIPTIVE_STATISTICS:
        return DerivedAnalysis(
            mode=mode,
            summary=f"已对 {len(rows)} 行结果完成描述统计，识别到 {len(metrics)} 个数值列。",
            metrics=metrics,
            limitations=limitations,
        )

    time_index = _time_index(columns, rows)
    if time_index is None:
        limitations.append("未识别到至少两个有效时间点，无法生成时序洞察。")
        return DerivedAnalysis(mode=mode, summary="结果缺少可用时间序列，未生成趋势、比较或异常结论。", metrics=metrics, limitations=limitations)

    time_column = columns[time_index]
    series, series_limitations = _series(columns, rows, time_index)
    limitations.extend(series_limitations)
    trends, comparisons, anomalies = [], [], []
    for column, points in series.items():
        if mode in {AnalysisMode.TREND, AnalysisMode.AUTO_INSIGHTS}:
            trends.append(_trend(column, time_column, points))
        if mode in {AnalysisMode.PERIOD_COMPARISON, AnalysisMode.AUTO_INSIGHTS}:
            comparisons.extend(_comparisons(column, points))
        if mode in {AnalysisMode.ANOMALY_DETECTION, AnalysisMode.AUTO_INSIGHTS}:
            anomalies.extend(_anomalies(column, points))

    if mode in {AnalysisMode.PERIOD_COMPARISON, AnalysisMode.AUTO_INSIGHTS} and comparisons:
        for item in comparisons:
            if item["percent_change"] is None:
                limitations.append(f"{item['column']} 的基期值为 0，未计算百分比变化。")
        if not any(item["kind"] == "year_over_year" for item in comparisons):
            limitations.append("当前结果中没有同月同日的上一年基期，未生成同比。")
    if mode in {AnalysisMode.ANOMALY_DETECTION, AnalysisMode.AUTO_INSIGHTS} and series and all(len(points) < 4 for points in series.values()):
        limitations.append("异常检测至少需要四个时间点。")

    parts = []
    if trends:
        lead = trends[0]
        label = {"up": "上升", "down": "下降", "flat": "持平"}[lead["direction"]]
        change = "无法计算百分比" if lead["percent_change"] is None else f"{abs(lead['percent_change']):.2f}%"
        parts.append(f"{lead['column']} 从 {lead['start_period']} 到 {lead['end_period']} {label} {change}")
    if comparisons:
        parts.append(f"生成 {len(comparisons)} 项环比/同比比较")
    if mode in {AnalysisMode.ANOMALY_DETECTION, AnalysisMode.AUTO_INSIGHTS}:
        parts.append(f"识别到 {len(anomalies)} 个候选异常点")
    summary = "；".join(parts) + "。" if parts else "可用数据不足，未生成可靠的结构化洞察。"
    return DerivedAnalysis(
        mode=mode,
        summary=summary,
        metrics=metrics,
        time_column=time_column,
        trends=trends,
        comparisons=comparisons,
        anomalies=anomalies[:50],
        limitations=list(dict.fromkeys(limitations)),
    )


__all__ = ["describe_result", "infer_analysis_mode"]
