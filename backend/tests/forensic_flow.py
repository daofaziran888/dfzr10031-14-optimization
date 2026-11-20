"""取证封存全流程模拟核对脚本。

运行方式：cd backend && .venv/bin/python -m tests.forensic_flow

覆盖场景：
1. 复用一个快照编号发起两次封存确认，靠封存序号分清各次封存；
2. 待核对不能跳过签确认直接交付，证据不齐备不能封存，重复确认不增加份数；
3. 封存发出后登记分区更正（确认人签错范围）→ 转待作废、证据隔离；
4. 申诉人已领取的原件仍可核对领取时的证据；新申请只能取有效分区补充件；
5. 已领取原件与新申请对照，核对后者可取的分区和证据编号；
6. 隔离分区按更正后范围重签 → 恢复封存，交付清单更新（新证据编号）；
7. 作废对象仍可审计、不可交付；
8. 数据导出消费封存证据清单，按状态决定拦放。
"""
from __future__ import annotations

from app.services.export import ExportService
from app.services.forensic import ForensicService
from app.store import store

forensic = ForensicService()
exporter = ExportService()

CHECKS: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    CHECKS.append(mark)
    print(f"[{mark}] {label}" + (f" —— {detail}" if detail else ""))
    if not condition:
        raise SystemExit(f"核对失败：{label}")


def partitions_of(entry: dict) -> dict[str, dict]:
    return {p["分区号"]: p for p in entry["partitions"]}


def main() -> None:
    # 清空取证封存表，保证模拟从干净状态开始（内存库，重启即恢复种子数据）
    store.rows("forensic").clear()

    # ---------- 第一轮：登记 → 确认 → 封存 → 领取 ----------
    entry, missing = forensic.create_entry({
        "快照编号": "SNAP-0003",
        "损益期间": "2026-09",
        "partitions": [
            {"分区号": "P1", "分区范围": "2026-09-01~2026-09-10", "行数": 1250, "校验哈希": "a1b2c3-p1"},
            {"分区号": "P2", "分区范围": "2026-09-11~2026-09-20", "行数": 1180, "校验哈希": "d4e5f6-p2"},
            {"分区号": "P3", "分区范围": "2026-09-21~2026-09-30", "行数": 980, "校验哈希": "g7h8i9-p3"},
        ],
    })
    assert entry is not None, missing
    seal_id = int(entry["id"])
    check("登记取证封存，初始状态为待核对", entry["status"] == "待核对")
    check("同一快照编号第一次封存，封存序号为 1", entry["封存序号"] == 1, entry["封存编号"])
    check("登记时读取分区校验记录", partitions_of(entry)["P1"]["校验记录"]["校验哈希"] == "a1b2c3-p1")

    # 待核对时不能跳过签确认直接交付
    _, msg = forensic.deliver(seal_id, {"领取人": "申诉人李雷"})
    check("待核对状态不能跳过签确认直接交付", "不能跳过签确认" in msg, msg)

    # 签署范围与分区范围不一致时确认被拦下
    _, msg = forensic.confirm_partition(seal_id, {"分区号": "P1", "对账人": "王对账", "签署范围": "2026-09-01~2026-09-15"})
    check("签署范围与分区范围不一致不能确认", "不一致" in msg, msg)

    # 确认 P1，再重复确认：份数不变
    entry, msg = forensic.confirm_partition(seal_id, {"分区号": "P1", "对账人": "王对账", "签署范围": "2026-09-01~2026-09-10"})
    assert entry is not None
    entry, msg = forensic.confirm_partition(seal_id, {"分区号": "P1", "对账人": "王对账", "签署范围": "2026-09-01~2026-09-10"})
    assert entry is not None
    p1 = partitions_of(entry)["P1"]
    check("重复确认不增加证据份数", "重复确认不增加证据份数" in msg and len(p1["证据历史"]) == 1, msg)

    # 证据未齐备不能封存
    _, msg = forensic.seal(seal_id)
    check("证据未齐备不能封存", "证据未齐备" in msg, msg)

    forensic.confirm_partition(seal_id, {"分区号": "P2", "对账人": "王对账", "签署范围": "2026-09-11~2026-09-20"})
    forensic.confirm_partition(seal_id, {"分区号": "P3", "对账人": "李复核", "签署范围": "2026-09-21~2026-09-30"})
    entry, msg = forensic.seal(seal_id)
    assert entry is not None
    check("证据齐备后封存，状态流转为已封存", entry["status"] == "已封存", msg)

    manifest = forensic.manifest(seal_id)
    assert manifest is not None
    check("已封存交付清单为整件 3 份", manifest["交付类型"] == "整件" and len(manifest["可交付证据"]) == 3)

    # 申诉人领取原封存材料（整件）
    entry, msg = forensic.deliver(seal_id, {"领取人": "申诉人李雷"})
    assert entry is not None
    receipt1 = entry["receipts"][0]
    check("申诉人已领取原封存整件", receipt1["交付类型"] == "整件" and len(receipt1["证据清单"]) == 3)

    # ---------- 封存发出后收到分区更正：P2 确认人签错范围 ----------
    entry, msg = forensic.register_correction(seal_id, {
        "分区号": "P2", "正确范围": "2026-09-11~2026-09-18", "说明": "确认人签错范围",
    })
    assert entry is not None
    p2 = partitions_of(entry)["P2"]
    check("登记更正后封存件转待作废", entry["status"] == "待作废", msg)
    check("签错分区证据被隔离", p2["证据状态"] == "签错隔离" and p2["证据编号"] == "EV-SNAP-0003-01-P2")
    check("更正后分区范围已更新", p2["分区范围"] == "2026-09-11~2026-09-18")

    manifest = forensic.manifest(seal_id)
    assert manifest is not None
    deliverable_numbers = [item["证据编号"] for item in manifest["可交付证据"]]
    check(
        "待作废交付清单为补充件，签错分区不在其列",
        manifest["交付类型"] == "补充件"
        and deliverable_numbers == ["EV-SNAP-0003-01-P1", "EV-SNAP-0003-01-P3"]
        and manifest["隔离证据"][0]["证据编号"] == "EV-SNAP-0003-01-P2",
        f"可交付={deliverable_numbers}",
    )

    # 新申请只能取有效分区补充件，签错分区不再随旧封存件发出
    entry, msg = forensic.deliver(seal_id, {"领取人": "新申请人王芳"})
    assert entry is not None
    receipt2 = entry["receipts"][1]
    got2 = [item["证据编号"] for item in receipt2["证据清单"]]
    check(
        "新申请领取补充件，不含签错分区",
        receipt2["交付类型"] == "补充件" and got2 == ["EV-SNAP-0003-01-P1", "EV-SNAP-0003-01-P3"],
        f"新申请取得={got2}",
    )

    # 已领取原件 vs 新申请对照
    compare = forensic.compare_receipt(seal_id, receipt1["领取编号"])
    assert compare is not None
    check(
        "对照：新申请可取 P1/P3 及原证据编号",
        [i["证据编号"] for i in compare["新申请可取"]] == ["EV-SNAP-0003-01-P1", "EV-SNAP-0003-01-P3"],
    )
    check(
        "对照：原领取中的签错分区对新申请不可取",
        len(compare["新申请不可取"]) == 1
        and compare["新申请不可取"][0]["证据编号"] == "EV-SNAP-0003-01-P2"
        and compare["新申请不可取"][0]["当前证据状态"] == "签错隔离",
    )

    # 已领取的材料仍能核对领取时的证据
    verify = forensic.verify_receipt(seal_id, receipt1["领取编号"])
    assert verify is not None
    p2_line = [d for d in verify["明细"] if d["分区号"] == "P2"][0]
    check(
        "已领取原件仍可核对领取时的证据（含已隔离分区）",
        verify["可核对"] and p2_line["当前证据状态"] == "签错隔离" and p2_line["哈希一致"],
    )

    # ---------- 重签 + 恢复，交付清单更新 ----------
    _, msg = forensic.recover(seal_id)
    check("隔离分区未重签前不能恢复封存", "未重签" in msg, msg)

    _, msg = forensic.resign_partition(seal_id, {"分区号": "P2", "对账人": "王对账", "签署范围": "2026-09-11~2026-09-20"})
    check("重签范围须与更正后范围一致", "更正后分区范围" in msg, msg)

    entry, msg = forensic.resign_partition(seal_id, {"分区号": "P2", "对账人": "王对账", "签署范围": "2026-09-11~2026-09-18"})
    assert entry is not None
    p2 = partitions_of(entry)["P2"]
    check(
        "重签生效，签发新证据编号，确认轮次递增",
        p2["证据编号"] == "EV-SNAP-0003-01-P2-R2" and p2["确认"]["确认轮次"] == 2 and p2["证据状态"] == "有效",
        p2["证据编号"],
    )

    entry, msg = forensic.recover(seal_id)
    assert entry is not None
    check("重签后恢复封存，状态回到已封存", entry["status"] == "已封存", msg)

    manifest = forensic.manifest(seal_id)
    assert manifest is not None
    deliverable_numbers = [item["证据编号"] for item in manifest["可交付证据"]]
    check(
        "恢复后交付清单更新：新证据编号入列，旧签错证据不再交付",
        manifest["交付类型"] == "整件"
        and deliverable_numbers == ["EV-SNAP-0003-01-P1", "EV-SNAP-0003-01-P2-R2", "EV-SNAP-0003-01-P3"]
        and "EV-SNAP-0003-01-P2" not in deliverable_numbers,
        f"可交付={deliverable_numbers}",
    )

    # 领取时的旧收据依然可核对（旧证据留在证据历史里）
    verify = forensic.verify_receipt(seal_id, receipt1["领取编号"])
    assert verify is not None
    check("恢复后原领取记录核对依然成立", verify["可核对"])

    # ---------- 第二轮：复用同一快照编号再封存 ----------
    entry2, missing = forensic.create_entry({
        "快照编号": "SNAP-0003",
        "损益期间": "2026-10",
        "partitions": [
            {"分区号": "P1", "分区范围": "2026-10-01~2026-10-15", "行数": 1300, "校验哈希": "x1y2z3-p1"},
            {"分区号": "P2", "分区范围": "2026-10-16~2026-10-31", "行数": 1290, "校验哈希": "u4v5w6-p2"},
        ],
    })
    assert entry2 is not None, missing
    check(
        "同一快照编号第二次封存，序号递增可区分",
        entry2["封存序号"] == 2 and entry2["封存编号"] == "SEAL-SNAP-0003-2" and entry2["封存编号"] != entry["封存编号"],
        entry2["封存编号"],
    )
    check("第二轮证据编号带本轮序号", entry2["partitions"][0]["证据编号"] == "EV-SNAP-0003-02-P1")

    seal2_id = int(entry2["id"])
    forensic.confirm_partition(seal2_id, {"分区号": "P1", "对账人": "王对账", "签署范围": "2026-10-01~2026-10-15"})
    forensic.confirm_partition(seal2_id, {"分区号": "P2", "对账人": "李复核", "签署范围": "2026-10-16~2026-10-31"})
    entry2, _ = forensic.seal(seal2_id)
    assert entry2 is not None
    check("第二轮封存完成", entry2["status"] == "已封存")

    rows, total = forensic.list_entries(keyword="SNAP-0003")
    check("同编号两次封存在册可分清", total == 2 and {r["封存序号"] for r in rows} == {1, 2})

    # ---------- 作废：对象仍可审计、不可交付 ----------
    entry2, _ = forensic.request_invalidate(seal2_id, {"说明": "期间口径调整"})
    assert entry2 is not None and entry2["status"] == "待作废"
    entry2, msg = forensic.confirm_invalidate(seal2_id)
    assert entry2 is not None
    check("确认作废后状态为已作废", entry2["status"] == "已作废", msg)
    check("作废对象仍可审计（明细与流转记录保留）", forensic.get_entry(seal2_id) is not None and len(entry2["流转记录"]) > 0)
    _, msg = forensic.deliver(seal2_id, {"领取人": "任何人"})
    check("已作废封存件不可交付", "不可交付" in msg, msg)
    manifest2 = forensic.manifest(seal2_id)
    assert manifest2 is not None
    check("已作废交付清单为不可交付", manifest2["交付类型"] == "不可交付" and not manifest2["可交付"])

    # ---------- 数据导出消费封存证据清单 ----------
    export_rows = store.rows("export")
    export_rows.append({"id": 9001, "任务编号": "EXPO-9001", "导出对象": "SEAL-SNAP-0003-1", "导出格式": "CSV",
                        "status": "待导出", "pending": True, "abnormal": False})
    export_rows.append({"id": 9002, "任务编号": "EXPO-9002", "导出对象": "SEAL-SNAP-0003-2", "导出格式": "CSV",
                        "status": "待导出", "pending": True, "abnormal": False})
    export_rows.append({"id": 9003, "任务编号": "EXPO-9003", "导出对象": "SNAP-0003", "导出格式": "CSV",
                        "status": "待导出", "pending": True, "abnormal": False})

    task, msg = exporter.run_action(9001, "开始导出")
    check("导出消费清单：已封存（第一轮）放行", task is not None and task["status"] == "导出中", msg)
    check("放行消息带可交付证据份数", "3份（整件）" in msg, msg)

    task, msg = exporter.run_action(9002, "开始导出")
    check("导出消费清单：已作废（第二轮）拦下", task is None and "不可交付" in msg, msg)

    task, msg = exporter.run_action(9003, "开始导出")
    check("按快照编号定位最新一次封存（第二轮已作废）拦下", task is None and "SEAL-SNAP-0003-2" in msg, msg)

    print(f"\n全部 {len(CHECKS)} 项核对通过。")


if __name__ == "__main__":
    main()
