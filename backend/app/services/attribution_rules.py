"""归因建模规范口径（核验码 113b54c4）。

触点权重配置的阈值、允许范围与上限都在这里判定；归因建模、指标下钻保存数据集
两个入口共用同一份口径，避免两边各算一遍导致结论对不上。
"""
from __future__ import annotations

from typing import Any

RULE_CODE = "113b54c4"
RULE_NAME = "归因建模规范口径"

# 规范口径（核验码 113b54c4）
WEIGHT_MIN = 0.0
WEIGHT_MAX = 0.8
SUM_MIN = 0.99
SUM_MAX = 1.01
MAX_TOUCHPOINTS = 8
SUM_EPSILON = 1e-6


class WeightRuleError(ValueError):
    """触点权重配置不满足规范口径时抛出，调用方据此给出可读说明。"""


def rule_spec() -> dict[str, Any]:
    """口径原文，供接口与页面展示，避免前端硬编码。"""
    return {
        "核验码": RULE_CODE,
        "名称": RULE_NAME,
        "单触点允许范围": [WEIGHT_MIN, WEIGHT_MAX],
        "权重合计阈值": [SUM_MIN, SUM_MAX],
        "触点数上限": MAX_TOUCHPOINTS,
    }


def _as_weights(weights: Any) -> dict[str, float]:
    if not isinstance(weights, dict):
        raise WeightRuleError("触点权重配置必须是「触点 → 权重」的映射")
    parsed: dict[str, float] = {}
    for touchpoint, value in weights.items():
        name = str(touchpoint).strip()
        if not name:
            raise WeightRuleError("触点名称不能为空")
        try:
            parsed[name] = float(value)
        except (TypeError, ValueError) as exc:
            raise WeightRuleError(f"触点「{name}」的权重不是数值：{value!r}") from exc
    return parsed


def validate_weights(weights: Any) -> dict[str, Any]:
    """按规范口径判定触点权重：触点数上限、单触点允许范围、权重合计阈值。"""
    result: dict[str, Any] = {
        "口径核验码": RULE_CODE,
        "口径名称": RULE_NAME,
        "触点数上限": MAX_TOUCHPOINTS,
        "单触点允许范围": [WEIGHT_MIN, WEIGHT_MAX],
        "权重合计阈值": [SUM_MIN, SUM_MAX],
        "触点数": 0,
        "权重合计": 0.0,
        "越界触点": [],
        "判定": "通过",
        "说明": "",
    }
    try:
        parsed = _as_weights(weights)
    except WeightRuleError as exc:
        result.update({"判定": "权重非法", "说明": str(exc)})
        return result

    result["触点数"] = len(parsed)
    total = round(sum(parsed.values()), 6)
    result["权重合计"] = total
    result["越界触点"] = [
        name for name, value in parsed.items() if value < WEIGHT_MIN or value > WEIGHT_MAX
    ]

    if not parsed:
        result.update({"判定": "权重非法", "说明": "触点权重配置为空"})
        return result
    if len(parsed) > MAX_TOUCHPOINTS:
        result.update({
            "判定": "超出上限",
            "说明": f"触点数 {len(parsed)} 超过上限 {MAX_TOUCHPOINTS}",
        })
        return result
    if result["越界触点"]:
        result.update({
            "判定": "超出允许范围",
            "说明": "触点 "
            + "、".join(result["越界触点"])
            + f" 的权重超出允许范围 [{WEIGHT_MIN}, {WEIGHT_MAX}]",
        })
        return result
    if total < SUM_MIN or total > SUM_MAX:
        result.update({
            "判定": "偏离阈值",
            "说明": f"触点权重合计 {total} 超出阈值 {SUM_MIN}~{SUM_MAX}，需按合计归一化后重算",
        })
        return result
    result["说明"] = f"触点数 {len(parsed)} 未超上限，单触点权重均在允许范围内，合计 {total} 在阈值内"
    return result


def normalized_weights(weights: dict[str, float]) -> dict[str, float]:
    """权重归一化：口径偏离时按合计归一，保证重算结果可复现。"""
    parsed = _as_weights(weights)
    total = sum(parsed.values())
    if total <= 0:
        raise WeightRuleError("触点权重合计必须大于 0")
    return {name: value / total for name, value in parsed.items()}


def build_detail(
    weights: dict[str, float],
    contribution_base: dict[str, Any],
) -> tuple[list[dict[str, Any]], float, str, str, list[str]]:
    """按规范口径重算指标贡献度，返回（明细、合计贡献度、排名、根因摘要、口径修正项）。"""
    normalized = normalized_weights(weights)
    corrections: list[str] = []
    validation = validate_weights(weights)
    if validation["判定"] == "偏离阈值":
        corrections.append(validation["说明"])
    elif validation["判定"] == "通过" and abs(validation["权重合计"] - 1.0) > SUM_EPSILON:
        corrections.append(f"触点权重合计 {validation['权重合计']} 未严格等于 1，按合计归一化后重算")

    bases: dict[str, float] = {}
    for touchpoint in normalized:
        raw = contribution_base.get(touchpoint, 0)
        try:
            bases[touchpoint] = float(raw)
        except (TypeError, ValueError):
            bases[touchpoint] = 0.0

    contributions = {
        name: round(bases[name] * weight, 2) for name, weight in normalized.items()
    }
    total = round(sum(contributions.values()), 2)
    ordered = sorted(
        contributions.items(), key=lambda item: (-item[1], item[0])
    )
    detail: list[dict[str, Any]] = []
    for rank, (name, contribution) in enumerate(ordered, start=1):
        share = round(contribution / total * 100, 2) if total else 0.0
        detail.append({
            "排名": rank,
            "触点": name,
            "触点权重": round(float(weights[name]), 4),
            "归一化权重": round(normalized[name], 6),
            "波动贡献基数": round(bases[name], 2),
            "波动贡献度": round(contribution, 2),
            "贡献度占比": share,
        })

    ranking = " > ".join(
        f"{item['排名']}.{item['触点']}({item['贡献度占比']}%)" for item in detail
    )
    top = detail[0] if detail else None
    if top is None:
        summary = "触点权重重算后没有可用贡献度"
    else:
        summary = f"{top['触点']} 贡献度最高（{top['贡献度占比']}%），是本次波动的首要根因"
        if corrections:
            summary += "；口径修正：" + "；".join(corrections)
    return detail, total, ranking, summary, corrections
