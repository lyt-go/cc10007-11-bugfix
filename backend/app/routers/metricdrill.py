"""指标归因接口：维护归因分析，覆盖发起分析、确认结论、归档分析与指标下钻数据集流程。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.attribution_rules import rule_spec
from app.services.metricdrill import MetricdrillService

router = APIRouter(prefix="/api/metricdrill", tags=["指标归因"])

service = MetricdrillService()

LIST_FIELDS = ["分析编号", "目标指标", "波动方向", "波动幅度", "维度下钻路径", "贡献度排名", "根因假设", "验证结果"]
STATUSES = ["草稿", "分析中", "已确认", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按分析编号检索"),
    status: str | None = Query(default=None, description="草稿、分析中、已确认、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按分析编号与状态过滤指标归因列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出指标归因清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "metricdrill", "total": total, "items": items}


@router.get("/rule")
def rule() -> dict[str, Any]:
    """归因建模规范口径（核验码 113b54c4）：阈值、允许范围与上限。"""
    return rule_spec()


@router.get("/datasets")
def datasets(analysis_no: str | None = Query(default=None, description="按指标分析编号过滤数据集")) -> dict[str, Any]:
    """指标波动归因明细数据集清单：按数据编号可复查每条数据集的落库凭证与作废情况。"""
    items = service.datasets(analysis_no)
    return {
        "module": "metricdrill",
        "total": len(items),
        "有效数据集": sum(1 for row in items if row.get("有效标记")),
        "items": items,
    }


@router.get("/datasets/{dataset_no}")
def trace_dataset(dataset_no: str) -> dict[str, Any]:
    """按数据编号复查数据集：模型状态、作废凭证与原告警时点贡献率一并返回。"""
    trace = service.trace_dataset(dataset_no)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"数据集 {dataset_no} 不存在")
    return trace


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条归因分析明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"归因分析 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/detail", response_model=dict)
def get_detail(entry_id: int) -> dict:
    """指标下钻详情：当前根因、有效数据集、待选模型、异常批次与落库凭证一次取齐。"""
    detail = service.detail(entry_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"归因分析 {entry_id} 不存在或已归档")
    return detail


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条归因分析，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.merged_values())
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="归因分析已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条归因分析执行发起分析、确认结论、归档分析、重新分析和数据集保存。"""
    values = payload.merged_values()
    action = str(values.get("action") or "").strip()
    entry, message, effects = service.run_action(
        entry_id,
        action,
        values=values,
        analyst=str(values.get("operator") or "值班管理员"),
        allow_supersede=bool(values.get("allow_supersede")),
    )
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry, effects=effects)


@router.post("/{entry_id}/datasets/settle", response_model=ActionResult)
def settle_dataset(entry_id: int, payload: EntryPayload) -> ActionResult:
    """处理等待中的指标波动归因明细数据集：校验口径与模型状态后落定。"""
    values = payload.merged_values()
    entry, message, effects = service.settle_dataset(
        entry_id,
        dataset_no=values.get("数据集编号"),
        analyst=str(values.get("operator") or "值班管理员"),
    )
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry, effects=effects)
