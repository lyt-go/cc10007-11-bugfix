"""启动自检：清掉历史数据里「模型已下线却还在支撑当前根因」的残留。

线上缺陷（核验码 1113141a）的表现是：指标分析记录里留着已下线模型产生的波动根因和
贡献度排名。种子数据本身是一致的，这里兜一层：任何一条指标分析如果用着非「已发布」
模型，就按与下线传播相同的口径把当前根因让位，只保留原告警时点快照。
"""
from __future__ import annotations

from typing import Any

from app.services import attribution_ledger as ledger
from app.store import store


def reconcile() -> list[dict[str, Any]]:
    """幂等自检：重复启动不会重复改写，也不会改动原告警时点快照。"""
    fixed: list[dict[str, Any]] = []
    with store.lock:
        for analysis in store.rows("metricdrill"):
            model_no = analysis.get("归因模型编号")
            if not model_no:
                continue
            model = ledger.find_model(str(model_no))
            if ledger.model_is_current(model):
                continue
            if analysis.get("根因状态") == "来源已失效":
                continue
            stamp = ledger.now_iso()
            analysis["原直接贡献率"] = analysis.get("原直接贡献率") or (model or {}).get("直接贡献率")
            analysis["原间接贡献率"] = analysis.get("原间接贡献率") or (model or {}).get("间接贡献率")
            analysis["失效前根因"] = {
                "归因模型编号": model_no,
                "归因模型版本": analysis.get("归因模型版本"),
                "数据集编号": analysis.get("数据集编号"),
                "根因假设": analysis.get("根因假设"),
                "贡献度排名": analysis.get("贡献度排名"),
                "原直接贡献率": analysis.get("原直接贡献率"),
                "原间接贡献率": analysis.get("原间接贡献率"),
                "记录时间": stamp,
            }
            analysis["根因状态"] = "来源已失效"
            analysis["根因假设"] = None
            analysis["贡献度排名"] = None
            analysis["验证结果"] = "待重新分析"
            analysis["验证人"] = None
            analysis["验证时间"] = None
            analysis["来源失效时间"] = analysis.get("来源失效时间") or stamp
            analysis["来源失效说明"] = analysis.get("来源失效说明") or (
                f"归因模型 {model_no} 当前状态为「{(model or {}).get('status', '不存在')}」，不再支撑新的指标根因"
            )
            analysis["待选模型"] = [
                int(row["id"]) for row in ledger.published_models(exclude_no=str(model_no))
            ]
            analysis["abnormal"] = True
            analysis["pending"] = True
            ledger.invalidate_datasets(
                str(analysis.get("分析编号")),
                reason=analysis["来源失效说明"],
                receipt_no="STARTUP-RECONCILE",
            )
            fixed.append({
                "指标分析编号": analysis.get("分析编号"),
                "原归因模型编号": model_no,
                "来源失效时间": analysis["来源失效时间"],
            })
    return fixed
