"""领域模型：每条结构化信息都带来源记录与原话片段。

设计原则
--------
1. 方言转写/原话与结构化判定并存，原话永不被结构化字段替换。
2. 凡身份或限定语存在两种以上有据可查的解释，登记为 :class:`Ambiguity`，
   待核实项交给经办人；当事人已明确的其他条件照常进入撮合。
3. "没说不去"不是同意：同意强度必须有本人明确表述支撑。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


def to_minutes(hhmm: str) -> int:
    """把 ``"18:10"`` 换算成当日分钟数，便于与报到截止时间比较。"""
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


# ---------------------------------------------------------------------------
# 面谈与意愿
# ---------------------------------------------------------------------------
class ConsentLevel(str, Enum):
    """本人同意强度。未明确表态只能是 ``UNSTATED``，不得升级为同意。"""

    UNSTATED = "未表态"          # 没说，不等于答应
    PREFERENCE = "偏好"          # "最好同车"，可接受其他安排
    CONDITIONAL = "附条件"       # 条件满足才接受，条件未满足前不算答应
    ACCEPTED = "已接受"          # 本人明确接受，可用于签约核对
    REJECTED = "明确拒绝"


class StatementType(str, Enum):
    """面谈原话按业务含义分类，分类结果均回链原句。"""

    COMPANION_PREFERENCE = "同车偏好"
    HARD_COMPANION = "同车为硬性条件"
    NIGHT_SHIFT_REFERRAL = "夜班意愿转问他人"
    DORMITORY_SELF = "宿舍自行解决"
    DORMITORY_OPEN = "宿舍未作承诺"
    PICKUP_CONDITION = "提出接站条件"
    NO_PROXY_SIGN = "禁止代签"
    NO_JOINT_AGREEMENT = "未共同约定"
    CONDITIONAL_JOB = "附条件接受岗位"
    CONFIRM_WILLINGNESS = "本人确认意愿"
    WEST_RECONFIRM = "西站需重新确认"


@dataclass(frozen=True)
class Ambiguity:
    """有据可查但存在两种解释的指代/限定语，留给经办人核实。"""

    subject_code: str
    item: str
    interpretations: tuple[str, ...]
    note: str
    source_id: str
    quote: str


@dataclass(frozen=True)
class Statement:
    """单句原话及其结构化判定。"""

    type: StatementType
    quote: str
    source_id: str
    consent: ConsentLevel = ConsentLevel.UNSTATED
    target_code: str | None = None          # 意愿指向的另一方
    job_id: str | None = None               # 条件指向的岗位
    station: str | None = None              # 限定的抵达站/接站点
    ambiguity: Ambiguity | None = None      # 本句遗留的待核实项


@dataclass(frozen=True)
class Person:
    code: str
    name: str
    source_id: str


@dataclass
class Interview:
    person: Person
    source_id: str
    original_text: str
    statements: list[Statement] = field(default_factory=list)
    ambiguities: list[Ambiguity] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 岗位
# ---------------------------------------------------------------------------
class ShiftType(str, Enum):
    FIXED_DAY = "固定白班"
    FIRST_NIGHT = "首日晚班"          # 后续"可申请转白班"不改变首日班次
    UNSTATED = "班次未注明"


@dataclass(frozen=True)
class Job:
    job_id: str
    title: str
    station: str
    deadline: str                       # 报到截止，如 "19:00"
    shift: ShiftType
    dormitory_promised: bool           # 宿舍是否构成雇主承诺
    source_id: str
    quote: str


# ---------------------------------------------------------------------------
# 接运与变更
# ---------------------------------------------------------------------------
class LegStatus(str, Enum):
    SCHEDULED = "计划中"
    TICKETED = "已出票"                # 硬承诺，先保
    CANCELLED = "已取消"
    REPLACED = "已换车"                # 原单仍可查，新单接续
    INFEASIBLE = "无法满足报到"


@dataclass(frozen=True)
class Seat:
    """席位上的人；``confirmed`` 为假表示只是预留，尚未获本人确认。"""

    person_code: str
    confirmed: bool
    source_id: str


@dataclass
class TransportLeg:
    leg_id: str
    carrier: str
    depart: str
    arrive: str
    station: str
    status: LegStatus
    seats: list[Seat] = field(default_factory=list)
    receiver_code: str | None = None
    pickup_point: str | None = None
    source_id: str = ""
    quote: str = ""
    supersedes: str | None = None       # 换车后指向被接续的原班次
    note: str = ""

    @property
    def capacity(self) -> int:
        """当前有效席位数（按变更后语料显式给出的席位/占座计算）。"""
        return len(self.seats)

    def available_for(self, person_code: str) -> bool:
        return all(s.person_code != person_code for s in self.seats)


@dataclass(frozen=True)
class LegChange:
    source_id: str
    quote: str
    cancelled: tuple[str, ...]
    replacements: tuple[dict, ...]      # 原始结构化片段，解析阶段直接落库
    infeasible: tuple[dict, ...]


# ---------------------------------------------------------------------------
# 本人确认（电话等）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Confirmation:
    source_id: str
    person_code: str
    quote: str
    companion_code: str | None
    station: str | None
    requires_receiver: bool
    west_must_reconfirm: bool


# ---------------------------------------------------------------------------
# 签认（合同）
# ---------------------------------------------------------------------------
class ContractStatus(str, Enum):
    READY = "可签"            # 雇主承诺与本人同意齐备，无条件或条件已满足
    HELD = "暂留"             # 条件未落实，不得签约但保留撮合
    BLOCKED = "不可签"        # 缺少本人同意等硬性前提


class ConditionCode(str, Enum):
    SAME_TRIP = "同趟同车"
    ARRIVE_STATION = "抵达指定站"
    RECEIVER = "到站有人接"
    ON_TIME = "报到期限内抵达"


@dataclass(frozen=True)
class EffectCondition:
    code: ConditionCode
    detail: str
    source_id: str
    quote: str
    companion_code: str | None = None
    station: str | None = None
    receiver_code: str | None = None


@dataclass
class Contract:
    contract_id: str
    person_code: str
    job_id: str
    source_id: str
    quote: str
    consent: ConsentLevel                      # 本人同意强度
    conditions: list[EffectCondition] = field(default_factory=list)
    status: ContractStatus = ContractStatus.HELD
    condition_results: list[str] = field(default_factory=list)
    dependent_legs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 接站单（领取/撤回）
# ---------------------------------------------------------------------------
class SheetStatus(str, Enum):
    CLAIMED = "已领取"
    WITHDRAWN = "已撤回"        # 承运方班次取消，旧单撤回但状态可查
    SUPERSEDED = "已被新单接替"
    REISSUED = "换发待确认"     # 换车后新单，上车点未核实前不得沿用旧点


@dataclass(frozen=True)
class StatusEvent:
    status: SheetStatus
    source_id: str
    note: str


@dataclass
class PickupSheet:
    sheet_id: str
    leg_id: str
    carrier: str
    contact_codes: tuple[str, ...]
    pickup_point: str | None
    receiver_code: str | None = None
    status: SheetStatus = SheetStatus.CLAIMED
    history: list[StatusEvent] = field(default_factory=list)
    source_id: str = ""


# ---------------------------------------------------------------------------
# 再确认任务与交接包
# ---------------------------------------------------------------------------
class ReconfirmStatus(str, Enum):
    OPEN = "待再确认"
    RESOLVED = "已确认"


@dataclass(frozen=True)
class Reconfirmation:
    task_id: str
    scope: str                  # 哪一段接运/哪一份合同
    station: str | None
    who: tuple[str, ...]        # 由谁确认（证件代号）
    reason: str
    source_id: str
    status: ReconfirmStatus = ReconfirmStatus.OPEN
    linked_contracts: tuple[str, ...] = ()


@dataclass(frozen=True)
class HandoverPacket:
    """交给接站方的最小信息包。

    仅含本段接运所需的联络码、时间、上车点与接站人；岗位信息与家庭原话
    留在驻点，不进入交接包。
    """

    contact_codes: tuple[str, ...]
    depart: str
    pickup_point: str | None
    receiver_code: str | None

    def as_dict(self) -> dict:
        return {
            "联络码": list(self.contact_codes),
            "发车时间": self.depart,
            "上车点": self.pickup_point or "待核实（不得沿用旧集合点）",
            "接站人": self.receiver_code or "本段暂无",
        }


# ---------------------------------------------------------------------------
# 解析阶段的完整事实集合
# ---------------------------------------------------------------------------
@dataclass
class CaseFacts:
    persons: dict[str, Person] = field(default_factory=dict)
    interviews: list[Interview] = field(default_factory=list)
    ambiguities: list[Ambiguity] = field(default_factory=list)
    jobs: dict[str, Job] = field(default_factory=dict)
    legs: dict[str, TransportLeg] = field(default_factory=dict)
    changes: list[LegChange] = field(default_factory=list)
    confirmations: list[Confirmation] = field(default_factory=list)
    contracts: list[Contract] = field(default_factory=list)
    sheets: list[PickupSheet] = field(default_factory=list)
    raw_records: list[dict] = field(default_factory=list)
