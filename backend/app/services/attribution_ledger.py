"""归因台账：数据集编号、落库凭证、处理终点与失效作废的公共写入。

归因建模（模型生命周期）和指标下钻（指标分析）两侧都改同一批记录，所以：
- 所有写操作在 store.lock 内完成，保证并发时只有一个处理终点、一个有效数据集；
- 每次落库都生成可追溯的凭证，业务结果以凭证 + 数据编号复查；
- 已下线模型不能再写入新的数据集，也不能继续充当当前根因。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.store import store

DATASET_MODULE = "attribution_dataset"
RECEIPT_MODULE = "attribution_receipt"
BATCH_MODULE = "attribution_batch"

VALID_DATASET = "有效"
INVALID_DATASET = "已作废"
PENDING_DATASET = "待处理"
SETTLED_DATASET = "已落定"

TERMINAL_FREEZE = "下线冻结"
TERMINAL_REPLACE = "替代发布"
TERMINAL_SINGLE = "单一处理终点"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_serial(module: str, prefix: str) -> str:
    """按同前缀现有最大序号生成下一个业务编号，例如 MDS-0003 / ATRC-0002。"""
    numbers: list[int] = []
    for row in store.rows(module):
        for field in ("数据集编号", "回执编号", "凭证编号"):
            raw = str(row.get(field) or "")
            if not raw.startswith(f"{prefix}-"):
                continue
            tail = raw.rsplit("-", 1)[-1]
            if tail.isdigit():
                numbers.append(int(tail))
    return f"{prefix}-{max(numbers, default=0) + 1:04d}"


def stable_digest(payload: Any) -> str:
    """把数据集内容压成可复查的摘要，同一输入永远得到同一摘要。"""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def find_model(model_no: str | None) -> dict[str, Any] | None:
    if not model_no:
        return None
    return store.find_by("attrmodel", "模型编号", str(model_no))


def model_is_current(model: dict[str, Any] | None) -> bool:
    """只有「已发布」的模型才允许支撑新的指标根因。"""
    return bool(model) and model.get("status") == "已发布"


def published_models(exclude_no: str | None = None) -> list[dict[str, Any]]:
    return [
        row
        for row in sorted(store.rows("attrmodel"), key=lambda item: int(item.get("id", 0)))
        if row.get("status") == "已发布" and row.get("模型编号") != exclude_no
    ]


def find_batch(analysis_no: str, model_no: str, model_version: Any) -> dict[str, Any] | None:
    """异常批次：按分析编号 + 模型 + 版本取最近一批，触点权重配置只从这里读。"""
    matches = [
        row
        for row in store.rows(BATCH_MODULE)
        if row.get("指标分析编号") == analysis_no
        and row.get("归因模型编号") == model_no
        and int(row.get("归因模型版本") or 0) == int(model_version or 0)
    ]
    if not matches:
        return None
    return max(matches, key=lambda row: str(row.get("异常时间") or ""))


def ledger_receipt(
    *,
    dataset_no: str,
    analysis_no: str,
    action: str,
    digest: str,
    operator: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成本次落库凭证：凭证编号、数据编号、摘要、时间都可回溯到同一条数据集。"""
    entry = {
        "凭证编号": make_serial(RECEIPT_MODULE, "LEDG"),
        "数据编号": dataset_no,
        "指标分析编号": analysis_no,
        "动作": action,
        "内容摘要": digest,
        "操作人": operator,
        "落库时间": now_iso(),
    }
    if extra:
        entry.update(extra)
    return entry


def claim_terminal(
    analysis: dict[str, Any],
    kind: str,
    token: str,
    *,
    summary: str,
    allow_supersede: bool,
) -> tuple[str, dict[str, Any] | None]:
    """给一条指标分析落定唯一处理终点。

    返回 (结果, 被取代的旧终点)。结果取值：
    - claimed：本次成为唯一处理终点；
    - unchanged：同一终点重复提交，幂等返回，不重复落库；
    - conflict：已有另一个处理终点，拒绝覆盖。
    """
    token_text = f"{kind}:{token}"
    current = analysis.get("已选处理终点")
    history = analysis.setdefault("处理终点历史", [])
    if current == token_text:
        return "unchanged", None
    if current:
        if not allow_supersede:
            history.append({
                "处理终点": token_text,
                "时间": now_iso(),
                "结果": "冲突未生效",
                "说明": f"指标分析 {analysis.get('分析编号')} 已由「{current}」处理，保留先到终点",
            })
            return "conflict", {"已选处理终点": current}
        previous = {"已选处理终点": current, "说明": summary}
        history.append({
            "处理终点": token_text,
            "时间": now_iso(),
            "结果": "取代前序终点",
            "说明": f"由「{token_text}」取代「{current}」",
        })
        analysis["已选处理终点"] = token_text
        analysis["上一处理终点"] = current
    else:
        analysis["已选处理终点"] = token_text
        history.append({
            "处理终点": token_text,
            "时间": now_iso(),
            "结果": "落定",
            "说明": summary,
        })
    analysis["处理终点时间"] = now_iso()
    analysis["处理终点说明"] = summary
    analysis["处理终点数量"] = 1
    return "claimed", None


def invalidate_datasets(
    analysis_no: str,
    *,
    reason: str,
    receipt_no: str,
    model_no: str | None = None,
    keep_dataset_no: str | None = None,
) -> list[dict[str, Any]]:
    """作废失效数据集：同一指标分析任何时候最多只留一个有效数据集。"""
    invalidated: list[dict[str, Any]] = []
    for dataset in store.rows(DATASET_MODULE):
        if dataset.get("指标分析编号") != analysis_no:
            continue
        if dataset.get("数据集编号") == keep_dataset_no:
            continue
        if dataset.get("状态") == INVALID_DATASET:
            continue
        if model_no and dataset.get("归因模型编号") != model_no:
            continue
        dataset["状态"] = INVALID_DATASET
        dataset["有效标记"] = False
        dataset["作废时间"] = now_iso()
        dataset["作废原因"] = reason
        dataset["作废凭证"] = receipt_no
        invalidated.append(dataset)
    return invalidated


def effective_dataset(analysis_no: str) -> dict[str, Any] | None:
    for dataset in store.rows(DATASET_MODULE):
        if dataset.get("指标分析编号") == analysis_no and dataset.get("有效标记"):
            return dataset
    return None


def dataset_by_no(dataset_no: str) -> dict[str, Any] | None:
    return store.find_by(DATASET_MODULE, "数据集编号", dataset_no)


def dataset_trace(dataset: dict[str, Any]) -> dict[str, Any]:
    """按数据编号复查：这条数据集用的模型现在是否还支撑当前指标根因。"""
    model = find_model(dataset.get("归因模型编号"))
    analysis = store.find_by("metricdrill", "分析编号", dataset.get("指标分析编号"))
    model_status = model.get("status") if model else "模型不存在"
    supports = bool(
        dataset.get("有效标记")
        and model_status == "已发布"
        and analysis is not None
        and analysis.get("数据集编号") == dataset.get("数据集编号")
        and analysis.get("根因状态") == "已重算"
    )
    return {
        "数据集编号": dataset.get("数据集编号"),
        "数据编号": dataset.get("数据编号") or dataset.get("数据集编号"),
        "指标分析编号": dataset.get("指标分析编号"),
        "归因模型编号": dataset.get("归因模型编号"),
        "归因模型当前状态": model_status,
        "数据集状态": dataset.get("状态"),
        "有效标记": bool(dataset.get("有效标记")),
        "当前是否支撑指标根因": supports,
        "复查结论": (
            "有效数据集，模型仍在已发布状态并支撑当前根因"
            if supports
            else "该数据集已失效或模型已下线，不再支撑当前指标根因"
        ),
        "落库凭证": dataset.get("落库凭证"),
    }
