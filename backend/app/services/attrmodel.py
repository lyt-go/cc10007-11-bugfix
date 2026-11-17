"""归因建模业务规则：状态流转、字段校验与筛选口径都收在这里。

生命周期口径：
- 只有「模型评审中」的模型才能发布，评审中不能直接跳到「已发布」以外的状态；
- 下线会沿指标下钻清单传播：引用它的指标分析转入「来源已失效」、待选模型就位、
  保留来源失效时间，原贡献度不再作为当前根因；
- 高并发下同一指标分析只落一个处理终点，已下线模型不能再支撑新的根因。
"""
from __future__ import annotations

from typing import Any

from app.services import attribution_ledger as ledger
from app.services.attribution_rules import RULE_CODE, rule_spec, validate_weights
from app.store import store

MODULE = "attrmodel"
REQUIRED_FIELDS = ["模型编号", "模型名称", "归因逻辑"]
STATUS_ORDER = ["草稿", "模型评审中", "已发布", "已下线"]
ACTION_RULES = {
    "提交模型评审": "模型评审中",
    "评审通过": None,  # 只写评审结论，不改状态；发布前必须通过
    "发布模型": "已发布",
    "下线模型": "已下线",
    "重新发布模型": "已发布",
}
NEGATIVE_ACTIONS = ["下线模型"]
# 动作允许的起始状态：评审中的模型没通过评审不能发布，已下线必须走重新发布
ACTION_SOURCE = {
    "提交模型评审": ["草稿", "已下线"],
    "评审通过": ["模型评审中"],
    "发布模型": ["模型评审中"],
    "下线模型": ["已发布"],
    "重新发布模型": ["已下线"],
}


class AttrmodelService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("模型编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        weights = values.get("触点权重分布")
        if weights is not None:
            verdict = validate_weights(weights)
            if verdict["判定"] == "权重非法":
                return None, [f"触点权重分布（{verdict['说明']}）"]
        with store.lock:
            rows = store.rows(MODULE)
            entry = {"id": store.next_id(MODULE)}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            entry["触点权重分布"] = weights or {}
            entry["衰减天数"] = values.get("衰减天数", 7)
            entry["转化窗口"] = values.get("转化窗口", 30)
            entry["直接贡献率"] = values.get("直接贡献率", 0)
            entry["间接贡献率"] = values.get("间接贡献率", 0)
            entry["评审结论"] = "待评审"
            entry["评审人"] = None
            entry["评审时间"] = None
            entry["下线时间"] = None
            entry["下线回执编号"] = None
            entry["版本"] = 1
            entry["status"] = STATUS_ORDER[0]
            entry["pending"] = True
            entry["abnormal"] = False
            rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        analyst: str = "值班管理员",
        replacement_model_no: str | None = None,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"归因模型 {entry_id} 不存在或已归档", None
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于归因建模可执行范围", None
        target = ACTION_RULES[action]
        if action != "评审通过" and target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里", None

        with store.lock:
            current = str(entry.get("status") or "")
            allowed_from = ACTION_SOURCE[action]
            if current not in allowed_from:
                if action == "发布模型" and current == "已下线":
                    return None, f"归因模型 {entry['模型编号']} 已下线，需先重新发布并确认替代关系", None
                if action == "发布模型":
                    return (
                        None,
                        f"归因模型 {entry['模型编号']} 当前状态为「{current}」，"
                        "发布模型只允许从「模型评审中」发起；评审中必须先完成评审结论再发布",
                        None,
                    )
                return (
                    None,
                    f"归因模型 {entry['模型编号']} 当前状态为「{current}」，"
                    f"「{action}」只允许从 {'、'.join(allowed_from)} 发起",
                    None,
                )

            effects: dict[str, Any] = {"action": action, "model": entry.get("模型编号")}

            if action == "评审通过":
                entry["评审结论"] = "通过"
                entry["评审人"] = analyst
                entry["评审时间"] = ledger.now_iso()
                entry["abnormal"] = False
                return entry, "归因模型已完成评审，可以发布", effects

            if action == "发布模型":
                if entry.get("评审结论") != "通过":
                    return (
                        None,
                        f"归因模型 {entry['模型编号']} 仍在模型评审中且没有通过的评审结论，不能直接发布",
                        None,
                    )
                entry["发布人"] = analyst
                entry["发布时间"] = ledger.now_iso()

            if action == "下线模型":
                receipt = self._downline(entry, analyst, replacement_model_no)
                effects.update(receipt)
                return entry, f"归因模型已下线，{receipt['传播摘要']}", effects

            if action == "重新发布模型":
                entry["status"] = "已发布"
                entry["pending"] = False
                entry["abnormal"] = False
                entry["版本"] = int(entry.get("版本") or 1) + 1
                entry["重新发布人"] = analyst
                entry["重新发布时间"] = ledger.now_iso()
                return entry, f"归因模型已重新发布为 v{entry['版本']}", effects

            entry["status"] = target
            entry["pending"] = target in {"草稿", "模型评审中"}
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            return entry, f"归因模型已{action}", effects

    # ------------------------------------------------------------------ 下线传播
    def _downline(
        self,
        model: dict[str, Any],
        analyst: str,
        replacement_model_no: str | None,
    ) -> dict[str, Any]:
        model_no = str(model.get("模型编号"))
        receipt_no = ledger.make_serial(ledger.RECEIPT_MODULE, "ATRC")
        stamp = ledger.now_iso()

        candidates = ledger.published_models(exclude_no=model_no)
        replacement = None
        if replacement_model_no:
            replacement = ledger.find_model(replacement_model_no)
            if not ledger.model_is_current(replacement):
                replacement = None
        if replacement is None and candidates:
            replacement = candidates[0]

        model["status"] = "已下线"
        model["pending"] = False
        model["abnormal"] = True
        model["下线时间"] = stamp
        model["下线回执编号"] = receipt_no
        model["下线人"] = analyst

        affected: list[dict[str, Any]] = []
        frozen: list[dict[str, Any]] = []
        for analysis in sorted(store.rows("metricdrill"), key=lambda row: int(row.get("id", 0))):
            if analysis.get("归因模型编号") != model_no:
                continue
            analysis_no = str(analysis.get("分析编号"))
            dataset_no = analysis.get("数据集编号")
            if analysis.get("根因状态") == "未分析" and not dataset_no:
                # 还没用这个模型支撑过根因的分析，不强行改写
                continue

            previous_root = analysis.get("根因假设")
            previous_ranking = analysis.get("贡献度排名")
            # 原告警时点快照只写一次：重复下线 / 重新分析都不能改动它
            if analysis.get("原直接贡献率") is None:
                analysis["原直接贡献率"] = model.get("直接贡献率")
            if analysis.get("原间接贡献率") is None:
                analysis["原间接贡献率"] = model.get("间接贡献率")
            analysis["根因状态"] = "来源已失效"
            analysis["验证结果"] = "待重新分析"
            analysis["验证人"] = None
            analysis["验证时间"] = None
            analysis["来源失效时间"] = stamp
            analysis["来源失效说明"] = (
                f"归因模型 {model_no} 已下线（回执 {receipt_no}），不再支撑新的指标根因"
            )
            analysis["待选模型"] = [int(row["id"]) for row in candidates]
            analysis["指标归属模块"] = MODULE
            analysis["abnormal"] = True
            analysis["pending"] = True
            analysis["失效前根因"] = {
                "归因模型编号": model_no,
                "归因模型版本": analysis.get("归因模型版本"),
                "数据集编号": dataset_no,
                "根因假设": previous_root,
                "贡献度排名": previous_ranking,
                "原直接贡献率": analysis.get("原直接贡献率"),
                "原间接贡献率": analysis.get("原间接贡献率"),
                "记录时间": stamp,
            }
            # 原结论保留在失效前根因里，当前根因必须让位：已下线模型不能继续支撑新根因
            analysis["根因假设"] = None
            analysis["贡献度排名"] = None

            dataset = ledger.dataset_by_no(str(dataset_no)) if dataset_no else None
            if dataset is not None and dataset.get("有效标记"):
                dataset["状态"] = ledger.INVALID_DATASET
                dataset["有效标记"] = False
                dataset["作废时间"] = stamp
                dataset["作废原因"] = analysis["来源失效说明"]
                dataset["作废凭证"] = receipt_no
                dataset["复查结论"] = "模型已下线，不再支撑当前指标根因"
                dataset.setdefault("失效前根因", analysis["失效前根因"])
                invalidated = [dataset]
            else:
                invalidated = ledger.invalidate_datasets(
                    analysis_no,
                    reason=analysis["来源失效说明"],
                    receipt_no=receipt_no,
                    model_no=model_no,
                    keep_dataset_no=dataset_no,
                )

            outcome, _ = ledger.claim_terminal(
                analysis,
                ledger.TERMINAL_FREEZE,
                model_no,
                summary=f"归档模型下线回执 {receipt_no}，先冻结下线模型结果",
                # 模型下线是权威事实：当前根因必须让位，前序终点记入历史而不是继续展示
                allow_supersede=True,
            )
            record = {
                "指标分析编号": analysis_no,
                "处理终点": analysis.get("已选处理终点"),
                "处理终点结果": outcome,
                "冻结处理": ledger.TERMINAL_FREEZE,
                "作废数据集": [row.get("数据集编号") for row in invalidated],
            }
            if outcome == "conflict":
                frozen.append(record)
            else:
                affected.append(record)

        receipt = {
            "回执编号": receipt_no,
            "归因模型编号": model_no,
            "归因模型版本": model.get("版本"),
            "下线时间": stamp,
            "操作人": analyst,
            "替代模型编号": replacement.get("模型编号") if replacement else None,
            "替代模型版本": replacement.get("版本") if replacement else None,
            "影响指标分析": [row["指标分析编号"] for row in affected],
            "处理终点冲突": frozen,
            "下游复算范围": sorted({row["指标分析编号"] for row in affected}),
            "状态": "已传播",
            "落库时间": stamp,
        }
        receipt["传播摘要"] = (
            f"{len(affected)} 条指标分析转入来源已失效"
            + (f"，替代模型 {receipt['替代模型编号']}" if receipt["替代模型编号"] else "，暂无可用替代模型")
            + (f"，{len(frozen)} 条已由其它处理终点先落定未改写" if frozen else "")
        )
        receipt["摘要"] = ledger.stable_digest({
            "回执": receipt_no,
            "模型": model_no,
            "分析": receipt["影响指标分析"],
        })

        with store.lock:
            store.rows(ledger.RECEIPT_MODULE).append(receipt)
            ditto_no = receipt_no
            for row in affected:
                row["回执编号"] = receipt_no
                row["出口"] = f"{MODULE} → metricdrill"
                row["已同步指标下钻清单"] = True
                row["同步时间"] = stamp
            for summary in (affected, frozen):
                for row in summary:
                    analysis = store.find_by("metricdrill", "分析编号", row["指标分析编号"])
                    if analysis is None:
                        continue
                    analysis["下线回执编号"] = receipt_no
                    analysis["下线模型编号"] = model_no
                    analysis["替代模型编号"] = receipt["替代模型编号"]
                    # 归因建模动作同步到指标下钻清单
                    analysis["指标下钻清单动作"] = f"归因建模-{model_no}-下线模型"
                    analysis["指标下钻清单同步时间"] = stamp
                    analysis["同步回执"] = ditto_no

        return {
            "回执编号": receipt_no,
            "传播摘要": receipt["传播摘要"],
            "影响指标分析": receipt["影响指标分析"],
            "替代模型编号": receipt["替代模型编号"],
            "下游复算范围": receipt["下游复算范围"],
            "处理终点冲突": receipt["处理终点冲突"],
            "口径核验码": RULE_CODE,
            "口径": rule_spec(),
        }

    # ------------------------------------------------------------------ 查询
    def receipts(self, model_no: str | None = None) -> list[dict[str, Any]]:
        rows = list(store.rows(ledger.RECEIPT_MODULE))
        if model_no:
            rows = [row for row in rows if row.get("归因模型编号") == model_no]
        return rows
