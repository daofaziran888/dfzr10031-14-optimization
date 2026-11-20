"""取证封存业务规则：分区证据、对账确认、状态流转与交付清单口径都收在这里。

交付政策（已明确）：采用「有效分区补充件交付」——封存件发出后若某分区因确认人签错范围
被隔离，其余仍有效的分区可以先以补充件形式交付给新申请；被隔离分区的证据编号进入隔离
清单，不再出现在任何新交付里；该分区按更正后范围重签生效后，以新的证据编号回到交付清单。
不采用「整件重签前暂停一切交付」：有效分区的证据链（校验记录 + 对账确认）未受污染，
停发会无必要地卡住合规申请。

状态流转：待核对 → 已封存 / 待作废；待作废 → 已封存（重签后恢复）/ 已作废（确认作废）。
已作废的封存件保留在册，仅供审计，不再交付。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "forensic"
REQUIRED_FIELDS = ["快照编号", "损益期间"]
STATUS_ORDER = ["待核对", "已封存", "待作废", "已作废"]
DELIVERY_POLICY = "有效分区补充件交付"

# 证据状态：待确认 → 有效 → 签错隔离 →（重签后）有效（新证据编号）
EV_PENDING = "待确认"
EV_VALID = "有效"
EV_QUARANTINED = "签错隔离"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ForensicService:
    # ---- 查询 ----
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
            rows = [
                row for row in rows
                if keyword in str(row.get("封存编号", "")) or keyword in str(row.get("快照编号", ""))
            ]
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

    # ---- 登记 ----
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        parts_in = values.get("partitions")
        if not isinstance(parts_in, list) or not parts_in:
            missing.append("分区明细")
        if missing:
            return None, missing

        rows = store.rows(MODULE)
        snap_no = str(values["快照编号"]).strip()
        # 同一快照编号可多次取证，封存序号递增，靠序号分清各次封存
        round_no = max(
            (int(row.get("封存序号", 0)) for row in rows if row.get("快照编号") == snap_no),
            default=0,
        ) + 1
        seal_no = f"SEAL-{snap_no}-{round_no}"

        partitions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for idx, raw in enumerate(parts_in, start=1):
            if not isinstance(raw, dict):
                return None, [f"第 {idx} 条分区明细格式不正确"]
            part_no = str(raw.get("分区号") or f"P{idx}").strip()
            if part_no in seen:
                return None, [f"分区号重复：{part_no}"]
            seen.add(part_no)
            part_range = str(raw.get("分区范围") or "").strip()
            if not part_range:
                return None, [f"分区{part_no}缺少分区范围"]
            check_hash = str(raw.get("校验哈希") or "").strip()
            if not check_hash:
                return None, [f"分区{part_no}缺少校验记录"]
            partitions.append({
                "分区号": part_no,
                "分区范围": part_range,
                "校验记录": {"行数": raw.get("行数"), "校验哈希": check_hash},
                "确认": None,
                "证据编号": f"EV-{snap_no}-{round_no:02d}-{part_no}",
                "证据状态": EV_PENDING,
                "更正记录": [],
                "证据历史": [],
            })

        entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "封存编号": seal_no,
            "快照编号": snap_no,
            "封存序号": round_no,
            "损益期间": str(values["损益期间"]).strip(),
            "数据集名称": str(values.get("数据集名称") or self._snapshot_dataset(snap_no) or "").strip(),
            "交付政策": DELIVERY_POLICY,
            "status": STATUS_ORDER[0],
            "pending": True,
            "abnormal": False,
            "partitions": partitions,
            "receipts": [],
            "流转记录": [],
            "登记时间": _now(),
            "交付清单更新时间": None,
        }
        self._trail(entry, "登记封存", f"第{round_no}次取证封存登记，读取{len(partitions)}个分区的分区范围与校验记录")
        rows.append(entry)
        return entry, []

    # ---- 动作分发 ----
    def run_action(self, entry_id: int, action: str, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        handlers = {
            "确认分区": lambda: self.confirm_partition(entry_id, values),
            "确认封存": lambda: self.seal(entry_id),
            "登记更正": lambda: self.register_correction(entry_id, values),
            "重签分区": lambda: self.resign_partition(entry_id, values),
            "恢复封存": lambda: self.recover(entry_id),
            "申请作废": lambda: self.request_invalidate(entry_id, values),
            "确认作废": lambda: self.confirm_invalidate(entry_id),
            "交付领取": lambda: self.deliver(entry_id, values),
        }
        handler = handlers.get(action)
        if handler is None:
            return None, f"动作「{action}」不属于取证封存可执行范围"
        return handler()

    # ---- 对账确认 ----
    def confirm_partition(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry, part, error = self._locate(entry_id, values)
        if error:
            return None, error
        assert entry is not None and part is not None
        if entry["status"] not in ("待核对", "待作废"):
            return None, f"当前状态「{entry['status']}」不能登记对账确认"
        if part["证据状态"] == EV_QUARANTINED:
            return None, f"分区{part['分区号']}证据已隔离，请通过「重签分区」恢复"
        # 重复确认幂等：已生效的证据不因重复确认增加份数
        if part["证据状态"] == EV_VALID and part.get("确认"):
            return entry, f"分区{part['分区号']}已完成确认，重复确认不增加证据份数"
        signer = str(values.get("对账人") or "").strip()
        signed_range = str(values.get("签署范围") or "").strip()
        if not signer or not signed_range:
            return None, "对账确认需要填写对账人与签署范围"
        if signed_range != part["分区范围"]:
            return None, f"签署范围与分区范围不一致：分区{part['分区号']}范围为{part['分区范围']}"
        part["确认"] = {"对账人": signer, "签署范围": signed_range, "确认时间": _now(), "确认轮次": 1}
        part["证据状态"] = EV_VALID
        part["证据历史"].append({
            "证据编号": part["证据编号"],
            "校验哈希": part["校验记录"]["校验哈希"],
            "签署范围": signed_range,
            "对账人": signer,
            "状态": EV_VALID,
            "时间": _now(),
            "说明": "对账确认生效",
        })
        self._trail(entry, "确认分区", f"分区{part['分区号']}由{signer}确认，证据{part['证据编号']}生效")
        return entry, f"分区{part['分区号']}确认完成，证据{part['证据编号']}生效"

    # ---- 封存 ----
    def seal(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"取证封存 {entry_id} 不存在或已归档"
        if entry["status"] != "待核对":
            return None, f"当前状态「{entry['status']}」不能确认封存"
        not_ready = [p["分区号"] for p in entry["partitions"] if not self._partition_ready(p)]
        if not_ready:
            return None, f"证据未齐备，不能封存：分区{'、'.join(not_ready)}缺少对账确认或校验记录"
        entry["status"] = "已封存"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["交付清单更新时间"] = _now()
        self._trail(entry, "确认封存", f"全部{len(entry['partitions'])}个分区证据齐备，封存生效，交付清单形成（整件可交付）")
        return entry, "封存完成，证据齐备，整件可交付"

    # ---- 分区更正（封存发出后发现签错范围） ----
    def register_correction(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry, part, error = self._locate(entry_id, values)
        if error:
            return None, error
        assert entry is not None and part is not None
        if entry["status"] not in ("已封存", "待作废"):
            return None, f"当前状态「{entry['status']}」不能登记分区更正"
        if part["证据状态"] != EV_VALID:
            return None, f"分区{part['分区号']}证据已隔离，不能重复登记更正"
        correct_range = str(values.get("正确范围") or "").strip()
        if not correct_range:
            return None, "登记更正需要填写正确范围"
        note = str(values.get("说明") or "确认人签错范围").strip()
        old_range = part["分区范围"]
        part["更正记录"].append({
            "时间": _now(),
            "原签署范围": (part.get("确认") or {}).get("签署范围"),
            "正确范围": correct_range,
            "说明": note,
        })
        part["分区范围"] = correct_range
        part["证据状态"] = EV_QUARANTINED
        for hist in part["证据历史"]:
            if hist["证据编号"] == part["证据编号"]:
                hist["状态"] = EV_QUARANTINED
        entry["status"] = "待作废"
        entry["pending"] = True
        entry["abnormal"] = True
        entry["交付清单更新时间"] = _now()
        self._trail(
            entry,
            "登记分区更正",
            f"分区{part['分区号']}{note}（{old_range}→{correct_range}），证据{part['证据编号']}隔离，"
            "封存件转待作废；按交付政策，仍有效分区可补充件交付",
        )
        return entry, f"分区{part['分区号']}证据已隔离，封存件转待作废；有效分区仍可补充件交付"

    # ---- 重签 ----
    def resign_partition(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry, part, error = self._locate(entry_id, values)
        if error:
            return None, error
        assert entry is not None and part is not None
        if entry["status"] != "待作废":
            return None, f"当前状态「{entry['status']}」不能重签分区"
        if part["证据状态"] != EV_QUARANTINED:
            return None, f"分区{part['分区号']}不在隔离状态，无需重签"
        signer = str(values.get("对账人") or "").strip()
        signed_range = str(values.get("签署范围") or "").strip()
        if not signer or not signed_range:
            return None, "重签需要填写对账人与签署范围"
        if signed_range != part["分区范围"]:
            return None, f"签署范围须与更正后分区范围一致（{part['分区范围']}）"
        round_no = int((part.get("确认") or {}).get("确认轮次", 1)) + 1
        old_evidence = part["证据编号"]
        part["确认"] = {"对账人": signer, "签署范围": signed_range, "确认时间": _now(), "确认轮次": round_no}
        part["证据编号"] = f"EV-{entry['快照编号']}-{int(entry['封存序号']):02d}-{part['分区号']}-R{round_no}"
        part["证据状态"] = EV_VALID
        part["证据历史"].append({
            "证据编号": part["证据编号"],
            "校验哈希": part["校验记录"]["校验哈希"],
            "签署范围": signed_range,
            "对账人": signer,
            "状态": EV_VALID,
            "时间": _now(),
            "说明": f"第{round_no}轮重签生效，取代{old_evidence}",
        })
        self._trail(entry, "重签分区", f"分区{part['分区号']}按更正后范围重签，新证据{part['证据编号']}，旧证据{old_evidence}保持隔离可审计")
        return entry, f"分区{part['分区号']}重签完成，新证据{part['证据编号']}"

    # ---- 恢复 ----
    def recover(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"取证封存 {entry_id} 不存在或已归档"
        if entry["status"] != "待作废":
            return None, f"当前状态「{entry['status']}」不能恢复封存"
        quarantined = [p["分区号"] for p in entry["partitions"] if p["证据状态"] == EV_QUARANTINED]
        if quarantined:
            return None, f"仍有隔离分区未重签：{'、'.join(quarantined)}"
        not_ready = [p["分区号"] for p in entry["partitions"] if not self._partition_ready(p)]
        if not_ready:
            return None, f"证据未齐备，不能恢复封存：分区{'、'.join(not_ready)}"
        entry["status"] = "已封存"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["交付清单更新时间"] = _now()
        self._trail(entry, "恢复封存", "隔离分区已完成重签，封存恢复生效，交付清单已更新（整件可交付）")
        return entry, "封存已恢复，交付清单已更新"

    # ---- 作废 ----
    def request_invalidate(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"取证封存 {entry_id} 不存在或已归档"
        if entry["status"] not in ("待核对", "已封存"):
            return None, f"当前状态「{entry['status']}」不能申请作废"
        note = str(values.get("说明") or "人工申请作废").strip()
        entry["status"] = "待作废"
        entry["pending"] = True
        entry["abnormal"] = True
        entry["交付清单更新时间"] = _now()
        self._trail(entry, "申请作废", note)
        return entry, "封存件已转待作废"

    def confirm_invalidate(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"取证封存 {entry_id} 不存在或已归档"
        if entry["status"] != "待作废":
            return None, f"当前状态「{entry['status']}」不能确认作废"
        entry["status"] = "已作废"
        entry["pending"] = False
        entry["abnormal"] = True
        self._trail(entry, "确认作废", "封存件作废，记录保留可供审计，不再交付")
        return entry, "封存件已作废，仍可审计"

    # ---- 交付领取 ----
    def deliver(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"取证封存 {entry_id} 不存在或已归档"
        receiver = str(values.get("领取人") or "").strip()
        if not receiver:
            return None, "交付领取需要填写领取人"
        if entry["status"] == "待核对":
            return None, "待核对状态不能跳过签确认直接交付"
        if entry["status"] == "已作废":
            return None, "已作废封存件不可交付，仅供审计"
        items = self._deliverable_items(entry)
        if not items:
            return None, "当前没有可交付的有效证据"
        delivery_type = "整件" if entry["status"] == "已封存" else "补充件"
        receipt = {
            "领取编号": f"RCV-{entry['封存编号']}-{len(entry['receipts']) + 1}",
            "领取人": receiver,
            "领取时间": _now(),
            "交付类型": delivery_type,
            "证据清单": items,
        }
        entry["receipts"].append(receipt)
        self._trail(entry, "交付领取", f"{receiver}领取{delivery_type}，含{len(items)}份证据")
        return entry, f"{receiver}已领取{delivery_type}（{len(items)}份证据）"

    # ---- 交付清单（数据导出消费） ----
    def manifest(self, entry_id: int) -> dict[str, Any] | None:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        items = self._deliverable_items(entry)
        quarantined = [
            {"分区号": p["分区号"], "证据编号": p["证据编号"], "原因": "签错范围，待重签"}
            for p in entry["partitions"]
            if p["证据状态"] == EV_QUARANTINED
        ]
        pending_parts = [p["分区号"] for p in entry["partitions"] if p["证据状态"] == EV_PENDING]
        if entry["status"] == "已封存":
            delivery_type, deliverable = "整件", bool(items)
        elif entry["status"] == "待作废":
            delivery_type, deliverable = "补充件", bool(items)
        else:
            delivery_type, deliverable = "不可交付", False
        return {
            "封存编号": entry["封存编号"],
            "快照编号": entry["快照编号"],
            "封存序号": entry["封存序号"],
            "status": entry["status"],
            "交付政策": DELIVERY_POLICY,
            "可交付": deliverable,
            "交付类型": delivery_type,
            "可交付证据": items,
            "隔离证据": quarantined,
            "待确认分区": pending_parts,
            "交付清单更新时间": entry.get("交付清单更新时间"),
        }

    # ---- 已领取原件 vs 新申请对照 ----
    def compare_receipt(self, entry_id: int, receipt_no: str) -> dict[str, Any] | None:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        receipt = self._find_receipt(entry, receipt_no)
        if receipt is None:
            return None
        manifest = self.manifest(entry_id)
        assert manifest is not None
        current_numbers = {item["证据编号"] for item in manifest["可交付证据"]}
        unavailable = [
            {**item, "当前证据状态": self._evidence_status(entry, item["证据编号"])}
            for item in receipt["证据清单"]
            if item["证据编号"] not in current_numbers
        ]
        return {
            "领取编号": receipt["领取编号"],
            "领取人": receipt["领取人"],
            "交付类型": receipt["交付类型"],
            "领取时间": receipt["领取时间"],
            "原领取证据": receipt["证据清单"],
            "新申请可取": manifest["可交付证据"],
            "新申请不可取": unavailable,
            "结论": (
                f"新申请当前可取{len(manifest['可交付证据'])}个分区（{manifest['交付类型']}）；"
                f"原领取中{len(unavailable)}份证据已隔离或取代，不再随旧封存件交付"
            ),
        }

    # ---- 领取时证据核对 ----
    def verify_receipt(self, entry_id: int, receipt_no: str) -> dict[str, Any] | None:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        receipt = self._find_receipt(entry, receipt_no)
        if receipt is None:
            return None
        details = []
        for item in receipt["证据清单"]:
            found = self._find_evidence(entry, item["证据编号"])
            details.append({
                "分区号": item["分区号"],
                "证据编号": item["证据编号"],
                "领取时校验哈希": item["校验哈希"],
                "当前证据状态": found["状态"] if found else "未找到",
                "哈希一致": bool(found and found["校验哈希"] == item["校验哈希"]),
            })
        ok = all(detail["哈希一致"] for detail in details)
        return {
            "领取编号": receipt["领取编号"],
            "可核对": ok,
            "明细": details,
            "结论": "领取时证据仍可核对" if ok else "存在核对不一致的证据",
        }

    # ---- 内部工具 ----
    def _snapshot_dataset(self, snap_no: str) -> str | None:
        for row in store.rows("snapshot"):
            if row.get("快照编号") == snap_no:
                return str(row.get("数据集名称") or "")
        return None

    def _locate(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, None, f"取证封存 {entry_id} 不存在或已归档"
        part_no = str(values.get("分区号") or "").strip()
        if not part_no:
            return None, None, "需要指定分区号"
        for part in entry["partitions"]:
            if part["分区号"] == part_no:
                return entry, part, None
        return None, None, f"分区{part_no}不存在于封存{entry['封存编号']}"

    @staticmethod
    def _partition_ready(part: dict[str, Any]) -> bool:
        return bool(
            part.get("确认")
            and part.get("证据状态") == EV_VALID
            and (part.get("校验记录") or {}).get("校验哈希")
        )

    def _deliverable_items(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        items = []
        for part in entry["partitions"]:
            if part["证据状态"] != EV_VALID or not part.get("确认"):
                continue
            items.append({
                "分区号": part["分区号"],
                "证据编号": part["证据编号"],
                "校验哈希": part["校验记录"]["校验哈希"],
                "签署范围": part["确认"]["签署范围"],
                "对账人": part["确认"]["对账人"],
            })
        return items

    def _find_receipt(self, entry: dict[str, Any], receipt_no: str) -> dict[str, Any] | None:
        for receipt in entry["receipts"]:
            if receipt["领取编号"] == receipt_no:
                return receipt
        return None

    def _find_evidence(self, entry: dict[str, Any], evidence_no: str) -> dict[str, Any] | None:
        for part in entry["partitions"]:
            if part["证据编号"] == evidence_no:
                return {"校验哈希": part["校验记录"]["校验哈希"], "状态": part["证据状态"]}
            for hist in part["证据历史"]:
                if hist["证据编号"] == evidence_no:
                    return {"校验哈希": hist["校验哈希"], "状态": hist["状态"]}
        return None

    def _evidence_status(self, entry: dict[str, Any], evidence_no: str) -> str:
        found = self._find_evidence(entry, evidence_no)
        return found["状态"] if found else "未找到"

    @staticmethod
    def _trail(entry: dict[str, Any], action: str, note: str) -> None:
        entry["流转记录"].append({"时间": _now(), "动作": action, "说明": note})


def deliverable_summary_for(ref: str) -> dict[str, Any] | None:
    """数据导出消费封存证据清单的入口：按封存编号或快照编号定位最新一次封存。

    返回 None 表示该导出对象与取证封存无关，导出流程不受约束。
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    rows = store.rows(MODULE)
    matches = [row for row in rows if row.get("封存编号") == ref]
    if not matches:
        matches = [row for row in rows if row.get("快照编号") == ref]
    if not matches:
        return None
    latest = max(matches, key=lambda row: int(row.get("封存序号", 0)))
    return ForensicService().manifest(int(latest["id"]))
