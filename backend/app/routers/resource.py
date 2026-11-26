"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services import service_for

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    allowed_filters = set(service.LIST_FIELDS) | set(getattr(service, "FILTER_FIELDS", set()))
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in allowed_filters and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    allowed_filters = set(service.LIST_FIELDS) | set(getattr(service, "FILTER_FIELDS", set()))
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in allowed_filters and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_id}/actions")
def run_action(module: str, entry_id: int, payload: ActionPayload) -> dict:
    service = require_service(module)
    # 领域服务返回完整字典：除 ok/message/entry 外，还可携带影响范围与重算回执。
    return service.handle_action(entry_id, payload.action.strip(), payload)


# 语义化动词别名：部分调用方/用例可能直接 POST /api/<module>/<id>/recompute 等路径。
VERB_METHODS = {
    "upgrade": "bump_version",
    "bump": "bump_version",
    "bump-version": "bump_version",
    "impact": "check_impact",
    "check-impact": "check_impact",
    "continue": "continue_old_recompute",
    "continue-old": "continue_old_recompute",
    "recompute": "recompute_baseline",
    "confirm": "confirm_price",
    "confirm-price": "confirm_price",
    "run": "run_compare",
    "run-compare": "run_compare",
    "compare": "run_compare",
}


@router.post("/{module}/{entry_id}/{verb}")
def run_verb(module: str, entry_id: int, verb: str) -> dict:
    service = require_service(module)
    method_name = VERB_METHODS.get(verb) or VERB_METHODS.get(verb.replace("_", "-"))
    method = getattr(service, method_name, None) if method_name else None
    if method is None:
        raise HTTPException(status_code=404, detail=f"未知操作：{verb}")
    return method(entry_id)


@router.get("/{module}/{entry_id}/impact")
def get_impact(module: str, entry_id: int) -> dict:
    service = require_service(module)
    method = getattr(service, "check_impact", None) or getattr(service, "impact_for_baseline", None)
    if method is None:
        raise HTTPException(status_code=404, detail=f"{module} 不支持影响范围核对")
    return method(entry_id)
