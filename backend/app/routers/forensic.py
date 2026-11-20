"""取证封存接口：登记取证封存、对账确认、封存/更正/重签/恢复/作废、交付领取与清单核对。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.forensic import ForensicService

router = APIRouter(prefix="/api/forensic", tags=["取证封存"])

service = ForensicService()

LIST_FIELDS = ["封存编号", "快照编号", "封存序号", "损益期间", "数据集名称", "交付政策"]
STATUSES = ["待核对", "已封存", "待作废", "已作废"]


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出取证封存清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "forensic", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按封存编号或快照编号检索"),
    status: str | None = Query(default=None, description="待核对、已封存、待作废、已作废"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按编号与状态过滤取证封存列表；已作废的封存件保留在列表里供审计。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {k: v for k, v in request.query_params.items() if k not in ("keyword", "status", "page", "size") and v.strip()}
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size, extra=extra)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条取证封存明细（含分区证据、领取记录、流转记录）；已作废的也可查。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"取证封存 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/manifest", response_model=dict)
def get_manifest(entry_id: int) -> dict:
    """交付清单：数据导出据此判断当前哪些状态可交付、能交哪些证据。"""
    manifest = service.manifest(entry_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"取证封存 {entry_id} 不存在或已归档")
    return manifest


@router.get("/{entry_id}/receipts/{receipt_no}/compare", response_model=dict)
def compare_receipt(entry_id: int, receipt_no: str) -> dict:
    """拿已领取原件和一份新申请对照：列出后者当前可取的分区与证据编号。"""
    result = service.compare_receipt(entry_id, receipt_no)
    if result is None:
        raise HTTPException(status_code=404, detail=f"领取记录 {receipt_no} 不存在")
    return result


@router.get("/{entry_id}/receipts/{receipt_no}/verify", response_model=dict)
def verify_receipt(entry_id: int, receipt_no: str) -> dict:
    """核对已领取材料：领取时冻结的证据清单与当前证据状态逐份对照。"""
    result = service.verify_receipt(entry_id, receipt_no)
    if result is None:
        raise HTTPException(status_code=404, detail=f"领取记录 {receipt_no} 不存在")
    return result


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一次取证封存：读取损益数据的分区范围与校验记录；同一快照编号可多次登记，按封存序号区分。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"取证封存已登记（{entry['封存编号']}）", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """执行确认分区、确认封存、登记更正、重签分区、恢复封存、申请作废、确认作废、交付领取。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
