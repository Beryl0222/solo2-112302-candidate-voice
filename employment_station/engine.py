"""撮合与签约把关引擎。

处理顺序严格按收件（``arrival_order``）推进：先应用接运变更与接站单
生命周期，再做席位分配与合同生效判定，最后生成再确认任务与交接包。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import (
    ConditionCode,
    ConsentLevel,
    Contract,
    ContractStatus,
    HandoverPacket,
    LegStatus,
    PickupSheet,
    Reconfirmation,
    ReconfirmStatus,
    Seat,
    SheetStatus,
    StatusEvent,
    TransportLeg,
    to_minutes,
)
from .parser import parse_corpus

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = _ROOT / "data" / "corpus.json"

# 交接包允许出现的字段（白名单）；岗位与家庭原话不在其中
_HANDOVER_KEYS = frozenset({"联络码", "发车时间", "上车点", "接站人"})


@dataclass
class Allocation:
    leg_id: str
    protected: tuple[str, ...]       # 已出票硬承诺保留的人
    placed: tuple[str, ...]          # 本轮实际分配的人
    rejected_pairs: tuple[tuple[str, ...], ...]  # 因席位不足未拆开的硬同行组合


class CaseBook:
    """经办人的工作台：保留全部原话与结构化判定，可逐项查询。"""

    def __init__(self, facts):
        self.facts = facts
        self.allocations: list[Allocation] = []
        self.reconfirmations: list[Reconfirmation] = []
        self._apply()

    # ------------------------------------------------------------------
    # 变更应用
    # ------------------------------------------------------------------
    def _apply(self) -> None:
        self._apply_leg_changes()
        self._sync_sheet_lifecycle()
        self._allocate_seats()
        self._evaluate_contracts()
        self._build_reconfirmations()

    def _active_legs(self) -> list[TransportLeg]:
        return [
            leg
            for leg in self.facts.legs.values()
            if leg.status
            not in (LegStatus.CANCELLED, LegStatus.REPLACED, LegStatus.INFEASIBLE)
        ]

    def _apply_leg_changes(self) -> None:
        for change in self.facts.changes:
            # 1) 取消的班次
            for carrier in change.cancelled:
                for leg in self.facts.legs.values():
                    if leg.carrier == carrier and leg.status is not LegStatus.CANCELLED:
                        leg.status = LegStatus.CANCELLED
                        leg.note = f"{change.source_id}：班次取消"

            # 2) 故障换车：原单保留可查，新单接续；硬承诺席位随人保留
            for rep in change.replacements:
                originals = [
                    leg
                    for leg in self.facts.legs.values()
                    if leg.carrier == rep["carrier"]
                    and leg.status not in (LegStatus.CANCELLED, LegStatus.REPLACED)
                ]
                for leg in originals:
                    carried = tuple(s.person_code for s in leg.seats)
                    leg.status = LegStatus.REPLACED
                    leg.note = f"{change.source_id}：故障换车，原接运单保留可查"
                    new_id = f"{leg.leg_id}-NEW"
                    # 变更语料显式给出剩余席位：超出容量的占座不能带过去
                    kept = carried[: rep["seats"]]
                    dropped = carried[rep["seats"] :]
                    new_leg = TransportLeg(
                        leg_id=new_id,
                        carrier=rep["carrier"],
                        depart=rep["depart"],
                        arrive=rep["arrive"],
                        station=rep["station"],
                        status=LegStatus.TICKETED,
                        seats=[
                            Seat(person_code=code, confirmed=False, source_id=change.source_id)
                            for code in kept
                        ],
                        receiver_code=None,
                        pickup_point=None,  # 上车点未核实，禁止沿用旧集合点
                        source_id=change.source_id,
                        quote=change.quote,
                        supersedes=leg.leg_id,
                        note=(
                            f"换车后仅剩{rep['seats']}席；旧集合点不得沿用到新接站单"
                            + (f"；{','.join(dropped)} 无座，不得拆对补录" if dropped else "")
                        ),
                    )
                    self.facts.legs[new_id] = new_leg

            # 3) 无法满足报到的备选班次
            for alt in change.infeasible:
                leg_id = f"ALT-{alt['station']}-{alt['depart']}"
                self.facts.legs[leg_id] = TransportLeg(
                    leg_id=leg_id,
                    carrier="",
                    depart=alt["depart"],
                    arrive=alt["depart"],
                    station=alt["station"],
                    status=LegStatus.INFEASIBLE,
                    seats=[],
                    source_id=change.source_id,
                    quote=change.quote,
                    note=(
                        f"{alt['station']}到报到地点需{alt['transfer_minutes']}分钟，"
                        "有效抵达晚于19:00报到截止"
                    ),
                )

    def _sync_sheet_lifecycle(self) -> None:
        for sheet in list(self.facts.sheets):
            leg = self.facts.legs.get(sheet.leg_id)
            if leg is None:
                continue
            if leg.status is LegStatus.CANCELLED:
                sheet.status = SheetStatus.WITHDRAWN
                sheet.history.append(
                    StatusEvent(
                        SheetStatus.WITHDRAWN,
                        leg.source_id,
                        f"承运方{sheet.carrier}班次取消，接站单撤回，状态留存可查",
                    )
                )
            elif leg.status is LegStatus.REPLACED:
                sheet.status = SheetStatus.SUPERSEDED
                sheet.history.append(
                    StatusEvent(
                        SheetStatus.SUPERSEDED,
                        leg.source_id,
                        "原班次故障换车，旧单领取状态可查，由新单接替",
                    )
                )
                new_leg_id = f"{leg.leg_id}-NEW"
                new_sheet = PickupSheet(
                    sheet_id=f"{sheet.sheet_id}-换发",
                    leg_id=new_leg_id,
                    carrier=sheet.carrier,
                    contact_codes=sheet.contact_codes,
                    pickup_point=None,  # 新单不得继续显示旧集合点
                    receiver_code=None,
                    status=SheetStatus.REISSUED,
                    history=[
                        StatusEvent(
                            SheetStatus.REISSUED,
                            leg.source_id,
                            "换发新接站单：上车点待核实，禁止沿用旧集合点",
                        )
                    ],
                    source_id=leg.source_id,
                )
                self.facts.sheets.append(new_sheet)

    # ------------------------------------------------------------------
    # 席位分配：先保硬承诺，剩余席位才给未确认组合；硬同行不得拆开
    # ------------------------------------------------------------------
    def _hard_tied_pairs(self) -> list[frozenset[str]]:
        """合同里写明"同趟/同车才生效"的硬同行组合（软偏好不在内）。"""
        pairs: set[frozenset[str]] = set()
        for contract in self.facts.contracts:
            for cond in contract.conditions:
                if cond.code is ConditionCode.SAME_TRIP and cond.companion_code:
                    pairs.add(
                        frozenset((contract.person_code, cond.companion_code))
                    )
        return list(pairs)

    def _allocate_seats(self) -> None:
        hard_pairs = self._hard_tied_pairs()
        for leg in self._active_legs():
            protected = tuple(s.person_code for s in leg.seats)
            occupied = set(protected)
            rejected: list[tuple[str, ...]] = []

            for pair in hard_pairs:
                members = tuple(sorted(pair))
                if any(m in occupied for m in members):
                    continue
                free = leg.capacity - len(occupied)
                if free >= len(members) and self._leg_meets_deadline(leg):
                    for code in members:
                        leg.seats.append(
                            Seat(person_code=code, confirmed=False, source_id=leg.source_id)
                        )
                        occupied.add(code)
                else:
                    # 只剩一席时不得拆开硬同行的两个人凑录用数
                    rejected.append(members)

            self.allocations.append(
                Allocation(
                    leg_id=leg.leg_id,
                    protected=protected,
                    placed=tuple(sorted(occupied - set(protected))),
                    rejected_pairs=tuple(rejected),
                )
            )

    def _leg_meets_deadline(self, leg: TransportLeg) -> bool:
        if leg.status is LegStatus.INFEASIBLE:
            return False
        deadline = min(
            (to_minutes(j.deadline) for j in self.facts.jobs.values()),
            default=19 * 60,
        )
        return to_minutes(leg.arrive) <= deadline

    # ------------------------------------------------------------------
    # 合同生效判定
    # ------------------------------------------------------------------
    def _evaluate_contracts(self) -> None:
        for contract in self.facts.contracts:
            # 硬前提：没有本人明确同意，不可签（"没说不去"不算答应）
            if contract.consent is ConsentLevel.UNSTATED:
                contract.status = ContractStatus.BLOCKED
                contract.condition_results.append("缺少本人明确同意，登记员不得代签")
                continue

            if not contract.conditions:
                # 独立合同：仅交通未落实也要保留，岗位本身可签
                contract.status = ContractStatus.READY
                contract.condition_results.append("岗位独立签认，无附加生效条件")
                contract.dependent_legs = self._legs_for_person(contract.person_code)
                continue

            results: list[str] = []
            all_met = True
            for cond in contract.conditions:
                met, detail = self._check_condition(contract, cond)
                results.append(f"{cond.code.value}：{'满足' if met else '未满足'}——{detail}")
                all_met = all_met and met
            contract.condition_results = results
            contract.status = ContractStatus.READY if all_met else ContractStatus.HELD
            contract.dependent_legs = self._legs_for_person(contract.person_code)

    def _check_condition(self, contract: Contract, cond: EffectCondition):
        active = self._active_legs()
        if cond.code is ConditionCode.SAME_TRIP:
            companion = cond.companion_code
            if not companion:
                return False, "同行对象指代待核实，留经办人确认"
            for leg in active:
                codes = {s.person_code for s in leg.seats}
                if (
                    contract.person_code in codes
                    and companion in codes
                    and leg.capacity >= 2
                    and self._leg_meets_deadline(leg)
                ):
                    return True, f"{leg.leg_id} 有匹配两席且双方在同趟"
            return False, (
                f"无与{companion}同趟的两席安排；不得拆开硬同行组合，"
                "须重新确认接运与同行范围"
            )

        if cond.code is ConditionCode.RECEIVER:
            for leg in active:
                codes = {s.person_code for s in leg.seats}
                if contract.person_code in codes and leg.receiver_code:
                    return True, f"{leg.leg_id} 接站人 {leg.receiver_code}"
            return False, "当前有效接运段无确认的接站人；“我要有人接”是条件而非已接受"

        if cond.code is ConditionCode.ARRIVE_STATION:
            for leg in active:
                codes = {s.person_code for s in leg.seats}
                if (
                    contract.person_code in codes
                    and leg.station == (cond.station or "东站")
                    and self._leg_meets_deadline(leg)
                ):
                    return True, f"{leg.leg_id} 于报到期限内抵达{leg.station}"
            return False, f"未能在报到期限内抵达{cond.station or '东站'}，依赖该段交通"

        if cond.code is ConditionCode.ON_TIME:
            for leg in active:
                codes = {s.person_code for s in leg.seats}
                if contract.person_code in codes and self._leg_meets_deadline(leg):
                    return True, f"{leg.leg_id} {leg.arrive} 抵达，未超截止时间"
            return False, "现有接运无法在报到期限内抵达"

        return False, "未知条件"

    def _legs_for_person(self, person_code: str) -> list[str]:
        result: list[str] = []
        for leg in self.facts.legs.values():
            if not any(s.person_code == person_code for s in leg.seats):
                continue
            # 原班次已被换车接续时，合同跟随当前有效班次，避免新旧单重复
            if leg.status is LegStatus.REPLACED:
                new_id = f"{leg.leg_id}-NEW"
                if new_id in self.facts.legs:
                    if new_id not in result:
                        result.append(new_id)
                    continue
            if leg.leg_id not in result:
                result.append(leg.leg_id)
        return result

    # ------------------------------------------------------------------
    # 再确认任务：写清哪一段、由谁确认
    # ------------------------------------------------------------------
    def _build_reconfirmations(self) -> None:
        seq = 1

        # 换车后仍有效的出票班次：本人接受新上车点/班次才能继续
        for leg in self._active_legs():
            if leg.supersedes and leg.status is LegStatus.TICKETED:
                who = tuple(sorted(s.person_code for s in leg.seats))
                self.reconfirmations.append(
                    Reconfirmation(
                        task_id=f"T{seq}",
                        scope=f"接运段 {leg.leg_id}（接续 {leg.supersedes}，{leg.depart}发/{leg.arrive}到{leg.station}）",
                        station=leg.station,
                        who=who,
                        reason="承运方故障换车，新单上车点待核实；晚到仍在报到期限内，本人接受即可换车继续",
                        source_id=leg.source_id,
                        linked_contracts=tuple(
                            c.contract_id
                            for c in self.facts.contracts
                            if c.person_code in who and not c.conditions
                        ),
                    )
                )
                seq += 1

        # 被取消班次上的硬同行组合
        cancelled_pairs: dict[frozenset[str], str] = {}
        for contract in self.facts.contracts:
            for cond in contract.conditions:
                if cond.code is ConditionCode.SAME_TRIP and cond.companion_code:
                    pair = frozenset((contract.person_code, cond.companion_code))
                    cancelled_pairs.setdefault(pair, contract.source_id)
        for pair, source in cancelled_pairs.items():
            members = tuple(sorted(pair))
            self.reconfirmations.append(
                Reconfirmation(
                    task_id=f"T{seq}",
                    scope="接运段 V1（17:00 C91 已取消）及替代班次的同行安排",
                    station="东站",
                    who=members,
                    reason=(
                        "同趟同车为合同生效条件，现无匹配两席；须由本人重新确认同行范围，"
                        "一席不得拆开硬同行的两个人"
                    ),
                    source_id=source,
                    linked_contracts=tuple(
                        c.contract_id
                        for c in self.facts.contracts
                        if c.person_code in pair
                    ),
                )
            )
            seq += 1

        # 西站方案：本人确认中明示须重新确认，且该段本身已超报到期限
        west = [
            leg
            for leg in self.facts.legs.values()
            if leg.station == "西站" and leg.status is LegStatus.INFEASIBLE
        ]
        west_who = tuple(
            sorted(
                {
                    c.person_code
                    for c in self.facts.confirmations
                    if c.west_must_reconfirm
                }
            )
        )
        if west and west_who:
            self.reconfirmations.append(
                Reconfirmation(
                    task_id=f"T{seq}",
                    scope="接运段 " + "、".join(leg.leg_id for leg in west) + "（西站备选）",
                    station="西站",
                    who=west_who,
                    reason=(
                        "本人确认：若是西站须先和站里重新确认；"
                        "且该段有效抵达19:20，已过19:00报到期限，确认后仍不可直接履约"
                    ),
                    source_id=west[0].source_id,
                    linked_contracts=tuple(
                        c.contract_id
                        for c in self.facts.contracts
                        if c.person_code in west_who
                    ),
                )
            )

    # ------------------------------------------------------------------
    # 经办人查询
    # ------------------------------------------------------------------
    def signable_jobs(self) -> list[Contract]:
        """可以签约的岗位（雇主承诺与本人同意齐备、条件满足）。"""
        return [c for c in self.facts.contracts if c.status is ContractStatus.READY]

    def held_contracts(self) -> list[Contract]:
        """只可暂留的合同：条件未落实，继续跟进而非取消。"""
        return [c for c in self.facts.contracts if c.status is ContractStatus.HELD]

    def blocked_contracts(self) -> list[Contract]:
        return [c for c in self.facts.contracts if c.status is ContractStatus.BLOCKED]

    def open_reconfirmations(self) -> list[Reconfirmation]:
        return [t for t in self.reconfirmations if t.status is ReconfirmStatus.OPEN]

    def sheets(self) -> list[PickupSheet]:
        return list(self.facts.sheets)

    def ambiguities(self):
        return list(self.facts.ambiguities)

    def leg(self, leg_id: str) -> TransportLeg:
        return self.facts.legs[leg_id]

    def handover_packet(self, sheet_id: str) -> HandoverPacket:
        """生成接站方交接包；撤回/已接替的旧单不得交接。"""
        sheet = next((s for s in self.facts.sheets if s.sheet_id == sheet_id), None)
        if sheet is None:
            raise KeyError(f"接站单不存在：{sheet_id}")
        if sheet.status in (SheetStatus.WITHDRAWN, SheetStatus.SUPERSEDED):
            raise ValueError(f"接站单 {sheet_id} 状态为{sheet.status.value}，不得交接")
        leg = self.facts.legs[sheet.leg_id]
        packet = HandoverPacket(
            contact_codes=sheet.contact_codes,
            depart=leg.depart,
            pickup_point=sheet.pickup_point,
            receiver_code=sheet.receiver_code,
        )
        # 白名单自检：任何多余字段都不允许出站
        assert frozenset(packet.as_dict()) == _HANDOVER_KEYS
        return packet


def build_casebook(records: list[dict] | None = None, path: str | Path | None = None) -> CaseBook:
    if records is None:
        target = Path(path) if path else DEFAULT_CORPUS
        records = json.loads(target.read_text(encoding="utf-8"))["records"]
    return CaseBook(parse_corpus(records))
