"""数据导出业务规则：状态流转、字段校验与筛选口径都收在这里。

导出对象若关联取证封存（封存编号或快照编号），开始导出前消费其封存证据清单：
只有清单判定为可交付（已封存整件 / 待作废补充件）时才放行，其余状态一律拦下。
"""
from __future__ import annotations

from typing import Any

from app.services.forensic import deliverable_summary_for
from app.store import store

MODULE = "export"
REQUIRED_FIELDS = ["任务编号", "导出对象", "导出格式"]
STATUS_ORDER = ["待导出", "导出中", "已完成", "导出失败"]
ACTION_RULES = {"开始导出": "导出中", "重新导出": "待导出", "标记失败": "导出失败"}
NEGATIVE_ACTIONS = []


class ExportService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"导出任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据导出可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        message = f"导出任务已{action}"
        if action == "开始导出":
            # 消费取证封存的证据清单：清单判定不可交付时拦下导出
            summary = deliverable_summary_for(str(entry.get("导出对象") or ""))
            if summary is not None:
                if not summary["可交付"]:
                    return None, (
                        f"封存{summary['封存编号']}当前状态「{summary['status']}」不可交付，"
                        "导出已拦下，请等待封存齐备或重签恢复"
                    )
                message += (
                    f"；封存{summary['封存编号']}可交付证据"
                    f"{len(summary['可交付证据'])}份（{summary['交付类型']}）"
                )
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, message
