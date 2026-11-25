"""经办人命令行：签约查询、再确认清单、接站单与交接包。

用法（项目根目录）::

    python3 -m employment_station report
    python3 -m employment_station reconfirm
    python3 -m employment_station sheets
    python3 -m employment_station handover M2-单-换发
"""
from __future__ import annotations

import argparse
import json

from .engine import build_casebook


def _print_header(title: str) -> None:
    print(f"\n=== {title} ===")


def cmd_report(book) -> None:
    _print_header("一、待核实的指代与限定语（经办人核实，本人其余条件照常撮合）")
    for amb in book.ambiguities():
        print(
            f"[{amb.source_id}] {amb.subject_code}：{amb.item}\n"
            f"    解释：{' / '.join(amb.interpretations)}；{amb.note}\n"
            f"    原话：{amb.quote}"
        )

    _print_header("二、可以签约的岗位（雇主承诺＋本人同意已核对）")
    for c in book.signable_jobs():
        job = book.facts.jobs[c.job_id]
        print(f"{c.contract_id}：{c.person_code} → {c.job_id} {job.title}（{c.status.value}）")
        for line in c.condition_results:
            print(f"    - {line}")
        if c.dependent_legs:
            print(f"    关联接运（仅交通待落实，合同保留）：{', '.join(c.dependent_legs)}")

    _print_header("三、只可暂留的合同（条件未落实，继续跟进不得取消）")
    for c in book.held_contracts():
        print(f"{c.contract_id}：{c.person_code} → {c.job_id}（{c.status.value}）")
        for line in c.condition_results:
            print(f"    - {line}")

    _print_header("四、席位分配（先保已出票硬承诺，剩余席位再给未确认组合）")
    for alloc in book.allocations:
        print(
            f"{alloc.leg_id}：硬承诺保留 {list(alloc.protected) or '无'}；"
            f"新增分配 {list(alloc.placed) or '无'}"
        )
        for pair in alloc.rejected_pairs:
            print(f"    - 硬同行 {list(pair)} 未获两席，未拆开凑数，转再确认")

    _print_header("五、接运再确认任务（哪一段、由谁确认）")
    for t in book.open_reconfirmations():
        print(
            f"{t.task_id} [{t.status.value}] {t.scope}\n"
            f"    确认人：{', '.join(t.who)}；关联合同：{', '.join(t.linked_contracts) or '无'}\n"
            f"    原因：{t.reason}"
        )

    _print_header("六、接站单生命周期（旧单领取/撤回可查，新单不带旧集合点）")
    for sheet in book.sheets():
        point = sheet.pickup_point or "待核实（禁止沿用旧集合点）"
        print(
            f"{sheet.sheet_id}：{sheet.carrier} / {sheet.leg_id} / {sheet.status.value} / 上车点：{point}"
        )


def cmd_reconfirm(book) -> None:
    for t in book.open_reconfirmations():
        print(
            f"{t.task_id}\t{t.scope}\t确认人={','.join(t.who)}\t"
            f"关联={','.join(t.linked_contracts) or '无'}\t{t.reason}"
        )


def cmd_sheets(book) -> None:
    for sheet in book.sheets():
        print(
            f"{sheet.sheet_id}\t{sheet.leg_id}\t{sheet.carrier}\t"
            f"{sheet.status.value}\t联络码={','.join(sheet.contact_codes) or '无'}"
        )
        for event in sheet.history:
            print(f"    {event.source_id} {event.status.value}：{event.note}")


def cmd_handover(book, sheet_id: str) -> None:
    try:
        packet = book.handover_packet(sheet_id)
    except (KeyError, ValueError) as exc:
        raise SystemExit(f"无法交接：{exc}")
    print(json.dumps(packet.as_dict(), ensure_ascii=False, indent=2))
    print("（仅含本段接运所需联络码、时间、上车点；岗位与家庭原话留驻点核对）")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="驻点就业服务站经办人工作台")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("report", help="完整工作台报告")
    sub.add_parser("reconfirm", help="接运再确认清单")
    sub.add_parser("sheets", help="接站单领取/撤回状态")
    hand = sub.add_parser("handover", help="生成接站方交接包")
    hand.add_argument("sheet_id", help="接站单编号，如 M2-单-换发")
    args = parser.parse_args(argv)

    book = build_casebook()
    if args.command == "report":
        cmd_report(book)
    elif args.command == "reconfirm":
        cmd_reconfirm(book)
    elif args.command == "sheets":
        cmd_sheets(book)
    elif args.command == "handover":
        cmd_handover(book, args.sheet_id)


if __name__ == "__main__":
    main()
