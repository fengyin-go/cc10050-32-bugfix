"""模块服务基类：共享查询、登记、动作和统计骨架，业务差异留在各模块文件。"""
from __future__ import annotations

from typing import Any

from app.store import store


class BaseModuleService:
    MODULE = ""
    ENTITY = ""
    LIST_FIELDS: list[str] = []
    # 列表字段之外允许参与精确/模糊过滤的关系字段（如站点名称、模板版本）。
    FILTER_FIELDS: list[str] = []
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER: list[str] = []
    ACTION_RULES: dict[str, str] = {}
    NEGATIVE_ACTIONS: list[str] = []

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(self.MODULE))
        filter_fields = set(self.LIST_FIELDS) | set(self.FILTER_FIELDS)
        for field, expected in (filters or {}).items():
            if field in filter_fields:
                rows = [
                    row for row in rows
                    if expected.casefold() in str(row.get(field, "")).casefold()
                ]
        if keyword and self.LIST_FIELDS:
            field = self.LIST_FIELDS[0]
            rows = [row for row in rows if keyword.casefold() in str(row.get(field, "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(self.MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing
        rows = store.rows(self.MODULE)
        entry_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        entry: dict[str, Any] = {"id": entry_id}
        # 先保留调用方提交的全部领域字段（模板版本、来源引用、价格关联等），
        # 再保证列表字段键存在，额外字段会随详情/列表一起返回。
        for key, value in (values or {}).items():
            if key in ("id",):
                continue
            entry[key] = value
        for field in self.LIST_FIELDS:
            entry.setdefault(field, values.get(field, ""))
        entry["id"] = entry_id
        entry.setdefault("status", self.STATUS_ORDER[0] if self.STATUS_ORDER else "")
        entry.setdefault("pending", True)
        entry.setdefault("abnormal", False)
        rows.append(entry)
        return entry, []

    def handle_action(
        self, entry_id: int, action: str, payload: Any | None = None
    ) -> dict[str, Any]:
        """动作统一入口：默认走状态流转，子服务可覆盖以返回富回执。"""
        entry, message = self.run_action(entry_id, action)
        return {"ok": entry is not None, "message": message, "entry": entry}

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        target = self.ACTION_RULES.get(action)
        if target is None:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
        entry["status"] = target
        entry["pending"] = target != self.STATUS_ORDER[-1]
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS
        return entry, f"{self.ENTITY}已{action}"

    def domain_snapshot(self) -> dict[str, Any]:
        """输出模块级数据质量快照，供跨模块消费和题位源码锚点使用。"""
        rows = store.rows(self.MODULE)
        by_status = {
            status: sum(1 for row in rows if row.get("status") == status)
            for status in self.STATUS_ORDER
        }
        return {
            "module": self.MODULE,
            "entity": self.ENTITY,
            "artifact": f"{self.ENTITY}数据集",
            "count": len(rows),
            "by_status": by_status,
            "abnormal": sum(1 for row in rows if row.get("abnormal")),
        }
