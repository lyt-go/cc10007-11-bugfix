"""指标归因业务规则：状态流转、字段校验与筛选口径都收在这里。

指标下钻的职责：
- 从异常批次读取触点权重配置，按规范口径（核验码 113b54c4）重算指标贡献度；
- 保存「缺陷关闭后的指标波动归因明细数据集」，等待处理 → 已落定；
- 来源失效的分析标出待选模型，保留来源失效时间，已下线模型不能继续支撑当前根因；
- 同一指标分析同时收到下线回执与新分析确认时只落一个处理终点，口径可先用参数切换，
  第一个到达的终点生效，后到的返回冲突而不是覆盖。
"""
from __future__ import annotations

from typing import Any

from app.services import attribution_ledger as ledger
from app.services.attribution_rules import (
    RULE_CODE,
    build_detail,
    rule_spec,
    validate_weights,
)
from app.store import store

MODULE = "metricdrill"
REQUIRED_FIELDS = ["分析编号", "目标指标", "波动方向"]
STATUS_ORDER = ["草稿", "分析中", "已确认", "已归档"]
ACTION_RULES = {
    "发起分析": "分析中",
    "确认结论": "已确认",
    "归档分析": "已归档",
    "保存归因明细数据集": None,  # 不直接改状态，走数据集流程
    "重新分析": None,
    "标记来源失效": None,
    "核对验证结果": None,  # 只写验证结论，不改分析状态
}
NEGATIVE_ACTIONS: list[str] = []


class MetricdrillService:
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
            rows = [row for row in rows if keyword in str(row.get("分析编号", ""))]
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
        with store.lock:
            entry = {"id": store.next_id(MODULE)}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            entry.update({
                "波动幅度": values.get("波动幅度"),
                "维度下钻路径": values.get("维度下钻路径"),
                "贡献度排名": None,
                "根因假设": None,
                "验证结果": None,
                "归因模型编号": None,
                "归因模型版本": None,
                "数据集编号": None,
                "根因状态": "未分析",
                "待选模型": [],
                "来源失效时间": None,
                "来源失效说明": None,
                "指标归属模块": None,
                "分析代码": values.get("分析编号"),
                "消息键": values.get("分析编号"),
                "已选处理终点": None,
                "处理终点时间": None,
                "原直接贡献率": values.get("直接贡献率", 0),
                "原间接贡献率": values.get("间接贡献率", 0),
                "复算范围": None,
                "status": STATUS_ORDER[0],
                "pending": True,
                "abnormal": False,
            })
            store.rows(MODULE).append(entry)
        return entry, []

    # ------------------------------------------------------------------ 动作入口
    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        values: dict[str, Any] | None = None,
        analyst: str = "值班管理员",
        allow_supersede: bool = False,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        values = values or {}
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"归因分析 {entry_id} 不存在或已归档", None
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于指标归因可执行范围", None

        if action == "保存归因明细数据集":
            return self._save_dataset(entry, values, analyst)
        if action == "重新分析":
            return self._reanalyze(entry, values, analyst, allow_supersede)
        if action == "标记来源失效":
            return self._mark_invalid(entry, values, analyst)
        if action == "核对验证结果":
            return self._verify(entry, values, analyst)

        target = ACTION_RULES[action]
        with store.lock:
            if action == "发起分析" and entry.get("status") not in {"草稿", "已归档"}:
                return None, f"归因分析 {entry.get('分析编号')} 当前状态为「{entry.get('status')}」，不能重复发起", None
            if action == "确认结论":
                blocker = self._root_blocker(entry)
                if blocker:
                    return None, blocker, None
            entry["status"] = target
            entry["pending"] = target in {"草稿", "分析中"}
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            return entry, f"归因分析已{action}", {"action": action}

    # ------------------------------------------------------------------ 数据集
    def _save_dataset(
        self,
        entry: dict[str, Any],
        values: dict[str, Any],
        analyst: str,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        analysis_no = str(entry.get("分析编号"))
        model_no = str(values.get("归因模型编号") or "").strip()
        if not model_no:
            return None, "保存归因明细数据集需要指定归因模型编号", None

        with store.lock:
            model = ledger.find_model(model_no)
            if model is None:
                return None, f"归因模型 {model_no} 不存在", None
            if not ledger.model_is_current(model):
                return (
                    None,
                    f"归因模型 {model_no} 当前状态为「{model.get('status')}」，已下线/未发布模型不能支撑新的指标根因",
                    None,
                )
            model_version = int(values.get("归因模型版本") or model.get("版本") or 1)
            batch = ledger.find_batch(analysis_no, model_no, model_version)
            if batch is None:
                return (
                    None,
                    f"异常批次缺失：指标分析 {analysis_no} 没有 {model_no} v{model_version} 的异常批次，无法读取触点权重配置",
                    None,
                )

            weights = batch.get("触点权重配置") or {}
            validation = validate_weights(weights)
            if validation["判定"] == "权重非法":
                return None, f"触点权重配置无法使用：{validation['说明']}", None

            existing = ledger.effective_dataset(analysis_no)
            # 同一分析 + 同一模型版本 + 同一异常批次 = 同一份数据集：无论待处理还是已落定，
            # 重复保存都返回原数据集，绝不生成第二份（并发下也只有先到的请求写库）。
            if (
                existing is not None
                and existing.get("归因模型编号") == model_no
                and int(existing.get("归因模型版本") or 0) == model_version
                and existing.get("异常批次编号") == batch.get("批次编号")
            ):
                return (
                    entry,
                    f"指标分析 {analysis_no} 已有数据集 {existing.get('数据集编号')}"
                    f"（{existing.get('状态')}），重复保存不会生成第二份",
                    {"dataset": existing, "dataset_no": existing.get("数据集编号"), "idempotent": True},
                )

            detail, total, ranking, summary, corrections = build_detail(
                weights, batch.get("波动贡献基数") or {}
            )
            serial = ledger.make_serial(ledger.DATASET_MODULE, "MDS")
            digest = ledger.stable_digest({
                "分析": analysis_no,
                "模型": model_no,
                "版本": model_version,
                "批次": batch.get("批次编号"),
                "权重": weights,
                "明细": detail,
            })
            receipt = ledger.ledger_receipt(
                dataset_no=str(serial),
                analysis_no=analysis_no,
                action="保存归因明细数据集",
                digest=digest,
                operator=analyst,
                extra={
                    "归因模型编号": model_no,
                    "归因模型版本": model_version,
                    "异常批次编号": batch.get("批次编号"),
                    "口径核验码": RULE_CODE,
                },
            )
            stamp = ledger.now_iso()
            dataset = {
                "id": store.next_id(ledger.DATASET_MODULE),
                "数据集编号": serial,
                "数据编号": serial,
                "指标分析编号": analysis_no,
                "消息键": f"{analysis_no}:{serial}",
                "分析代码": analysis_no,
                "归因模型编号": model_no,
                "归因模型版本": model_version,
                "异常批次编号": batch.get("批次编号"),
                "异常批次时间": batch.get("异常时间"),
                "触点权重配置": weights,
                "口径核验码": RULE_CODE,
                "口径校验": validation,
                "口径修正项": corrections,
                "贡献度明细": detail,
                "贡献度排名": ranking,
                "波动贡献度合计": total,
                "根因摘要": summary,
                "原直接贡献率": entry.get("原直接贡献率"),
                "原间接贡献率": entry.get("原间接贡献率"),
                "状态": ledger.PENDING_DATASET,
                "有效标记": True,
                "内容摘要": digest,
                "落库凭证": receipt,
                "保存人": analyst,
                "保存时间": stamp,
            }
            if existing is not None:
                ledger.invalidate_datasets(
                    analysis_no,
                    reason=f"被数据集 {serial} 重新保存取代",
                    receipt_no=receipt["凭证编号"],
                    keep_dataset_no=serial,
                )
                dataset["取代数据集"] = existing.get("数据集编号")
            store.rows(ledger.DATASET_MODULE).append(dataset)
            store.rows(ledger.RECEIPT_MODULE).append(receipt)

            entry["数据集编号"] = serial
            entry["消息键"] = f"{analysis_no}:{serial}"
            entry["指标归属模块"] = MODULE
            entry["归因模型编号"] = model_no
            entry["归因模型版本"] = model_version
            entry["贡献度排名"] = ranking
            entry["根因假设"] = summary
            entry["根因状态"] = "已重算"
            entry["验证结果"] = "待复核"
            entry["待选模型"] = [
                int(row["id"]) for row in ledger.published_models(exclude_no=model_no)
            ]
            entry["abnormal"] = False
            entry["pending"] = True
            entry["复算范围"] = {
                "指标分析编号": analysis_no,
                "数据集编号": serial,
                "触点范围": [item["触点"] for item in detail],
                "重算触点贡献度": total,
            }
            # 数据集等待处理：状态推进到分析中，等落定后再确认结论
            if entry.get("status") in {"草稿", "已归档"}:
                entry["status"] = "分析中"

            return (
                entry,
                f"指标波动归因明细数据集 {serial} 已保存，等待处理",
                {
                    "dataset": dataset,
                    "dataset_no": serial,
                    "receipt": receipt,
                    "validation": validation,
                    "pending": True,
                },
            )

    def settle_dataset(
        self,
        entry_id: int,
        *,
        dataset_no: str | None = None,
        analyst: str = "值班管理员",
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        """处理等待中的数据集：校验口径与模型状态后置为已落定。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"归因分析 {entry_id} 不存在或已归档", None
        with store.lock:
            serial = dataset_no or str(entry.get("数据集编号") or "")
            dataset = ledger.dataset_by_no(serial)
            if dataset is None or dataset.get("指标分析编号") != entry.get("分析编号"):
                return None, f"指标分析 {entry.get('分析编号')} 下没有数据集 {serial}", None
            if dataset.get("状态") == ledger.SETTLED_DATASET:
                return entry, f"数据集 {serial} 已落定，重复处理不会生成新记录", {
                    "dataset": dataset,
                    "idempotent": True,
                }
            if not dataset.get("有效标记"):
                return None, f"数据集 {serial} 已作废（{dataset.get('作废原因')}），不能落定", None
            model = ledger.find_model(dataset.get("归因模型编号"))
            if not ledger.model_is_current(model):
                return None, f"数据集 {serial} 的归因模型已失效，需重新分析后再落定", None

            digest = ledger.stable_digest({
                "数据集": serial,
                "明细": dataset.get("贡献度明细"),
                "口径": dataset.get("口径校验"),
            })
            receipt = ledger.ledger_receipt(
                dataset_no=serial,
                analysis_no=str(entry.get("分析编号")),
                action="数据集落定",
                digest=digest,
                operator=analyst,
                extra={"归因模型编号": dataset.get("归因模型编号"), "口径核验码": RULE_CODE},
            )
            stamp = ledger.now_iso()
            dataset["状态"] = ledger.SETTLED_DATASET
            dataset["落定时间"] = stamp
            dataset["落定凭证"] = receipt
            store.rows(ledger.RECEIPT_MODULE).append(receipt)

            entry["验证结果"] = "已验证"
            entry["pending"] = False
            entry["数据集状态"] = ledger.SETTLED_DATASET
            entry["数据集落定时间"] = stamp
            return (
                entry,
                f"数据集 {serial} 已落定，凭证 {receipt['凭证编号']}",
                {"dataset": dataset, "receipt": receipt},
            )

    # ------------------------------------------------------------------ 重新分析
    def _reanalyze(
        self,
        entry: dict[str, Any],
        values: dict[str, Any],
        analyst: str,
        allow_supersede: bool,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        analysis_no = str(entry.get("分析编号"))
        model_no = str(values.get("归因模型编号") or "").strip()
        with store.lock:
            if not model_no:
                candidates = ledger.published_models()
                if not candidates:
                    return None, "当前没有已发布的替代归因模型，无法重新分析", None
                model_no = str(candidates[0].get("模型编号"))
            model = ledger.find_model(model_no)
            if not ledger.model_is_current(model):
                return (
                    None,
                    f"归因模型 {model_no} 当前状态为「{model.get('status') if model else '不存在'}」，"
                    "已下线模型不能继续支撑新的指标根因",
                    None,
                )
            if entry.get("根因状态") == "已重算" and entry.get("归因模型编号") == model_no:
                return (
                    entry,
                    f"指标分析 {analysis_no} 当前根因已由 {model_no} 重算，无需重复重新分析",
                    {"idempotent": True},
                )

            model_version = int(values.get("归因模型版本") or model.get("版本") or 1)
            # 先确认新模型的异常批次可用，避免落了处理终点却拿不到触点权重
            batch = ledger.find_batch(analysis_no, model_no, model_version)
            if batch is None:
                return (
                    None,
                    f"异常批次缺失：指标分析 {analysis_no} 没有 {model_no} v{model_version} 的异常批次",
                    None,
                )

            current_terminal = str(entry.get("已选处理终点") or "")
            # 下线冻结只是等待替代分析的过渡终点：新的分析可以覆盖它，冻结记录进历史；
            # 若当前根因已由另一个替代模型发布，再换模型必须显式确认后重跑，
            # 保证指标页任何时点只展示一种当前结论。
            awaiting_replacement = current_terminal.startswith(f"{ledger.TERMINAL_FREEZE}:")
            supersede_ok = allow_supersede or awaiting_replacement
            outcome, conflict = ledger.claim_terminal(
                entry,
                ledger.TERMINAL_REPLACE,
                model_no,
                summary=f"发布替代分析 {model_no} v{model_version}",
                allow_supersede=supersede_ok,
            )
            if outcome == "conflict":
                return (
                    None,
                    f"指标分析 {analysis_no} 已由「{current_terminal}」先落定，"
                    "当前根因不能被替代分析覆盖；如需改用新模型请确认后重跑",
                    {"conflict": conflict, "已选处理终点": current_terminal},
                )

            # 作废旧来源数据集，保持「一条指标分析最多一个有效数据集」
            invalidated = ledger.invalidate_datasets(
                analysis_no,
                reason=f"替代分析改用 {model_no} v{model_version}，旧数据集不再支撑当前根因",
                receipt_no=current_terminal or f"REAN-{analysis_no}",
            )
            entry["来源失效时间"] = None
            entry["来源失效说明"] = None

            entry["归因模型编号"] = model_no
            entry["归因模型版本"] = model_version
            entry["根因状态"] = "重算中"
            entry["待选模型"] = [int(row["id"]) for row in ledger.published_models(exclude_no=model_no)]
            entry["验证结果"] = "待复核"
            entry["abnormal"] = True
            entry["pending"] = True
            entry["重新分析人"] = analyst
            entry["重新分析时间"] = ledger.now_iso()
            entry["替代模型编号"] = model_no
            scope = entry.get("复算范围")
            entry["复算范围"] = {
                **(scope if isinstance(scope, dict) else {}),
                "指标分析编号": analysis_no,
                "替代模型": f"{model_no} v{model_version}",
                "下游复算范围": "触点贡献度 > 贡献度排名 > 根因假设",
            }

            # 立即按新模型的异常批次重算并保存数据集
            entry_after, message, effects = self._save_dataset(
                entry, {"归因模型编号": model_no, "归因模型版本": model_version}, analyst
            )
            if entry_after is None:
                return None, message, effects
            effects = dict(effects or {})
            effects.update({
                "invalidated": [row.get("数据集编号") for row in invalidated],
                "terminal": entry.get("已选处理终点"),
                "outcome": outcome,
            })
            return entry_after, f"已按 {model_no} 重新分析；{message}", effects

    # ------------------------------------------------------------------ 来源失效
    def _mark_invalid(
        self,
        entry: dict[str, Any],
        values: dict[str, Any],
        analyst: str,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        with store.lock:
            if entry.get("根因状态") == "来源已失效":
                return entry, f"指标分析 {entry.get('分析编号')} 已标记来源失效，保留原失效时间", {
                    "idempotent": True,
                    "来源失效时间": entry.get("来源失效时间"),
                }
            model = ledger.find_model(entry.get("归因模型编号"))
            if ledger.model_is_current(model):
                return None, f"归因模型 {model.get('模型编号')} 仍在支撑根因，不能标记来源失效", None

            stamp = ledger.now_iso()
            analysis_no = str(entry.get("分析编号"))
            dataset_no = entry.get("数据集编号")
            reason = str(values.get("原因") or f"来源模型 {entry.get('归因模型编号')} 已失效")
            entry["原直接贡献率"] = entry.get("原直接贡献率") or (model or {}).get("直接贡献率")
            entry["原间接贡献率"] = entry.get("原间接贡献率") or (model or {}).get("间接贡献率")
            entry["失效前根因"] = {
                "归因模型编号": entry.get("归因模型编号"),
                "归因模型版本": entry.get("归因模型版本"),
                "数据集编号": dataset_no,
                "根因假设": entry.get("根因假设"),
                "贡献度排名": entry.get("贡献度排名"),
                "原直接贡献率": entry.get("原直接贡献率"),
                "原间接贡献率": entry.get("原间接贡献率"),
                "记录时间": stamp,
            }
            entry["根因状态"] = "来源已失效"
            entry["根因假设"] = None
            entry["贡献度排名"] = None
            entry["验证结果"] = "待重新分析"
            entry["验证人"] = None
            entry["验证时间"] = None
            entry["来源失效时间"] = stamp
            entry["来源失效说明"] = reason
            entry["待选模型"] = [
                int(row["id"]) for row in ledger.published_models(exclude_no=model_no)
            ]
            entry["指标归属模块"] = MODULE
            entry["abnormal"] = True
            entry["pending"] = True
            invalidated = ledger.invalidate_datasets(
                analysis_no,
                reason=reason,
                receipt_no=f"MANUAL-{analysis_no}",
                keep_dataset_no=None,
            )
            return entry, "指标分析已标记来源失效，待选模型已就位", {
                "invalidated": [row.get("数据集编号") for row in invalidated],
                "待选模型": entry["待选模型"],
            }

    def _verify(
        self,
        entry: dict[str, Any],
        values: dict[str, Any],
        analyst: str,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        """核对验证结果：只有当前根因确实由有效模型支撑时才能记为已验证。"""
        with store.lock:
            blocker = self._root_blocker(entry)
            if blocker:
                return None, f"无法核对验证结果：{blocker}", None
            if entry.get("验证结果") == "已验证":
                return entry, f"指标分析 {entry.get('分析编号')} 验证结果已核对，无需重复", {
                    "idempotent": True,
                    "验证结果": "已验证",
                }
            entry["验证结果"] = "已验证"
            entry["验证人"] = analyst
            entry["验证时间"] = ledger.now_iso()
            entry["待选模型"] = [
                int(row["id"])
                for row in ledger.published_models(exclude_no=entry.get("归因模型编号"))
            ]
            return entry, f"验证结果已核对：{entry.get('归因模型编号')} 仍支撑当前根因", {
                "验证结果": "已验证",
                "根因假设": entry.get("根因假设"),
                "贡献度排名": entry.get("贡献度排名"),
            }

    def _root_blocker(self, entry: dict[str, Any]) -> str | None:
        """确认结论前的守卫：已下线模型、失效来源、未落定数据集都不能支撑当前根因。"""
        analysis_no = entry.get("分析编号")
        if entry.get("根因状态") == "来源已失效":
            return f"指标分析 {analysis_no} 的来源模型已失效，需先按待选模型重新分析"
        if entry.get("根因状态") != "已重算":
            return f"指标分析 {analysis_no} 还没有由已发布模型重算的当前根因，不能确认结论"
        model = ledger.find_model(entry.get("归因模型编号"))
        if entry.get("归因模型编号") and not ledger.model_is_current(model):
            return f"归因模型 {entry.get('归因模型编号')} 已下线，不能继续支撑指标分析 {analysis_no} 的当前根因"
        if entry.get("数据集编号"):
            dataset = ledger.dataset_by_no(str(entry.get("数据集编号")))
            if dataset is None or not dataset.get("有效标记"):
                return f"数据集 {entry.get('数据集编号')} 已作废，不能确认结论"
            if dataset.get("状态") == ledger.PENDING_DATASET:
                return f"数据集 {entry.get('数据集编号')} 仍在等待处理，需先落定再确认结论"
        return None

    # ------------------------------------------------------------------ 查询
    def detail(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        analysis_no = str(entry.get("分析编号"))
        dataset = ledger.effective_dataset(analysis_no)
        receipts = [
            row for row in store.rows(ledger.RECEIPT_MODULE)
            if row.get("指标分析编号") == analysis_no
        ]
        batch = None
        if entry.get("归因模型编号"):
            batch = ledger.find_batch(
                analysis_no, str(entry.get("归因模型编号")), entry.get("归因模型版本")
            )
        candidates = [
            {
                "id": row.get("id"),
                "模型编号": row.get("模型编号"),
                "模型名称": row.get("模型名称"),
                "版本": row.get("版本"),
                "状态": row.get("status"),
                "直接贡献率": row.get("直接贡献率"),
                "间接贡献率": row.get("间接贡献率"),
            }
            for row in store.rows("attrmodel")
            if int(row.get("id", 0)) in {int(item) for item in entry.get("待选模型") or []}
        ]
        current_model = ledger.find_model(entry.get("归因模型编号"))
        return {
            "分析": entry,
            "数据集": dataset,
            "待选模型": candidates,
            "异常批次": batch,
            "落库凭证": receipts,
            "当前模型状态": current_model.get("status") if current_model else None,
            "当前根因可用": bool(
                entry.get("根因状态") == "已重算"
                and dataset is not None
                and dataset.get("有效标记")
                and ledger.model_is_current(current_model)
            ),
            "口径": rule_spec(),
        }

    def datasets(self, analysis_no: str | None = None) -> list[dict[str, Any]]:
        rows = list(store.rows(ledger.DATASET_MODULE))
        if analysis_no:
            rows = [row for row in rows if row.get("指标分析编号") == analysis_no]
        return rows

    def trace_dataset(self, dataset_no: str) -> dict[str, Any] | None:
        """从数据编号复查：这条数据集现在是否还在支撑当前指标根因。"""
        dataset = ledger.dataset_by_no(dataset_no)
        if dataset is None:
            return None
        analysis = store.find_by(MODULE, "分析编号", dataset.get("指标分析编号"))
        trace = ledger.dataset_trace(dataset)
        trace.update({
            "数据集状态": dataset.get("状态"),
            "落库凭证": dataset.get("落库凭证"),
            "作废凭证": dataset.get("作废凭证"),
            "作废时间": dataset.get("作废时间"),
            "作废原因": dataset.get("作废原因"),
            "贡献度排名": dataset.get("贡献度排名"),
            "口径校验": dataset.get("口径校验"),
            "异常批次编号": dataset.get("异常批次编号"),
            "当前分析根因状态": analysis.get("根因状态") if analysis else None,
            "当前分析数据集编号": analysis.get("数据集编号") if analysis else None,
            "当前分析根因假设": analysis.get("根因假设") if analysis else None,
            "原告警时点直接贡献率": (analysis or {}).get("原直接贡献率"),
            "原告警时点间接贡献率": (analysis or {}).get("原间接贡献率"),
        })
        return trace
