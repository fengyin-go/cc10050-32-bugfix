"""运维/回执接口：查询重算回执，并提供语义化的升级、影响核对、继续重算入口。

页面主链路仍走统一的 ``POST /api/{module}/{id}/actions``；
这组接口是等价的 REST 别名，方便直接对接或回查回执。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import relations
from app.services.price_baseline import PriceBaselineService
from app.services.price_compare import PriceCompareService
from app.services.page_parser import PageParserService
from app.store import store

router = APIRouter(prefix="/api/ops", tags=["影响范围与重算回执"])

_parser = PageParserService()
_baseline = PriceBaselineService()
_compare = PriceCompareService()


class IdListPayload(BaseModel):
    sample_ids: list[int] = []


@router.get("/receipts")
def list_receipts() -> dict[str, Any]:
    return {"ok": True, "items": relations.list_receipts(), "total": len(relations.list_receipts())}


@router.get("/receipts/{identifier}")
def get_receipt(identifier: str) -> dict[str, Any]:
    receipt = relations.get_receipt(identifier)
    if receipt is None:
        raise HTTPException(status_code=404, detail="回执不存在")
    return {"ok": True, "receipt": receipt}


@router.get("/impact/page_parser/{entry_id}")
def parser_impact(entry_id: int) -> dict[str, Any]:
    return _parser.check_impact(entry_id)


@router.post("/page_parser/{entry_id}/upgrade")
def parser_upgrade(entry_id: int) -> dict[str, Any]:
    return _parser.bump_version(entry_id)


@router.post("/page_parser/{entry_id}/impact")
def parser_check_impact(entry_id: int) -> dict[str, Any]:
    return _parser.check_impact(entry_id)


@router.get("/price_baseline/{entry_id}/impact")
def baseline_impact(entry_id: int) -> dict[str, Any]:
    return _baseline.impact_for_baseline(entry_id)


@router.post("/price_baseline/{entry_id}/continue")
def baseline_continue(entry_id: int) -> dict[str, Any]:
    return _baseline.continue_old_recompute(entry_id)


@router.post("/price_baseline/{entry_id}/confirm")
def baseline_confirm(entry_id: int) -> dict[str, Any]:
    return _baseline.confirm_price(entry_id)


@router.post("/price_baseline/{entry_id}/guard-mix")
def baseline_guard_mix(entry_id: int, payload: IdListPayload) -> dict[str, Any]:
    return _baseline.guard_mixed_versions(entry_id, payload.sample_ids)


@router.post("/price_compare/{entry_id}/run")
def compare_run(entry_id: int) -> dict[str, Any]:
    return _compare.run_compare(entry_id)


@router.post("/reset-scenario")
def reset_scenario() -> dict[str, Any]:
    """恢复内存示例数据（主要用于自检/演示）。"""
    from app.seed import SEED_ROWS

    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}  # noqa: SLF001
    relations._receipts.clear()  # noqa: SLF001
    relations._receipt_seq = 0  # noqa: SLF001
    return {"ok": True, "message": "场景数据已重置"}
