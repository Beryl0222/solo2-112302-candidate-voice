"""把语料原话解析为结构化事实。

解析器按记录类型分派，规则只依赖原话中的明示措辞与已解析出的证件代号，
不硬编码人名结论。原话整段保留在 ``original_text``/``quote`` 字段中，
方言转写与普通话补述都不被覆盖。
"""
from __future__ import annotations

import re

from .models import (
    Ambiguity,
    CaseFacts,
    Confirmation,
    ConsentLevel,
    Contract,
    EffectCondition,
    ConditionCode,
    Interview,
    Job,
    LegChange,
    LegStatus,
    Person,
    PickupSheet,
    Seat,
    SheetStatus,
    Statement,
    StatementType,
    StatusEvent,
    TransportLeg,
    ShiftType,
)

_CODE_RE = re.compile(r"C\d+")
_PERSON_CODE_RE = re.compile(r"C(?!9\d)\d+")  # 人员代号，排除 C9x 承运方
_CJK = r"㐀-䶿一-鿿"
_NAME_RE = re.compile(rf"^([{_CJK}]{{2,4}})，(?:证件代号)?(C\d+)[:：]")
_TIME_RE = re.compile(r"\d{1,2}:\d{2}")
_JOB_RE = re.compile(r"J\d+")
_CARRIER_RE = re.compile(r"C9\d")
_CONTACT_RE = re.compile(r"X\d+")
_RECEIVER_RE = re.compile(r"接站人(E\d+)")


# ---------------------------------------------------------------------------
# 面谈
# ---------------------------------------------------------------------------
def _split_clauses(text: str) -> list[str]:
    """按句号/分号/逗号切分，使同一长句中的多个意愿各自成条、原话不丢。"""
    parts = re.split(r"[。；，,]", text)
    return [p.strip() for p in parts if p.strip()]


def _resolve_name(text: str, persons: dict[str, Person]) -> str | None:
    """把原话中的姓名解析为证件代号；查不到则返回 None（留待核实）。"""
    for person in persons.values():
        if person.name and person.name in text:
            return person.code
    return None


def _parse_interview(
    record: dict,
    persons: dict[str, Person],
    name_to_job: dict[str, str],
) -> Interview:
    text = record["text"]
    _, code = _NAME_RE.match(text).groups()
    body = text.split("：", 1)[1]
    interview = Interview(
        person=persons[code], source_id=record["id"], original_text=text
    )
    other_codes = tuple(sorted(set(persons) - {code}))

    for clause in _split_clauses(body):
        # 夜班意愿转问他人：问的是别人，不能记成本人接受夜班
        if "问她" in clause or "你问" in clause:
            ambiguity = Ambiguity(
                subject_code=code,
                item="夜班能否上岗是谁的意愿（原话转问他人）",
                interpretations=other_codes,
                note="夜班能否上岗须问本人，登记员不得代为同意",
                source_id=record["id"],
                quote=clause,
            )
            interview.ambiguities.append(ambiguity)
            interview.statements.append(
                Statement(
                    StatementType.NIGHT_SHIFT_REFERRAL,
                    clause,
                    record["id"],
                    target_code=_resolve_name(clause, persons),
                    ambiguity=ambiguity,
                )
            )
            continue

        if "同车" in clause or "同趟" in clause:
            target = _resolve_name(clause, persons)
            target = target or (
                _PERSON_CODE_RE.findall(clause)[0]
                if _PERSON_CODE_RE.findall(clause)
                else None
            )
            hard = ("若" in clause) or ("的话" in clause) or ("才" in clause)
            ambiguity = None
            # 仅出现代词"她"而无姓名/代号：存在两种有据解释，留经办人核实
            if target is None and ("她" in clause or "对方" in clause):
                ambiguity = Ambiguity(
                    subject_code=code,
                    item="“她/对方”与谁同车",
                    interpretations=other_codes,
                    note="指代有两种有据解释，留经办人核实；本人其余明确条件仍可撮合",
                    source_id=record["id"],
                    quote=clause,
                )
                interview.ambiguities.append(ambiguity)
            interview.statements.append(
                Statement(
                    StatementType.HARD_COMPANION if hard else StatementType.COMPANION_PREFERENCE,
                    clause,
                    record["id"],
                    consent=ConsentLevel.CONDITIONAL if hard else ConsentLevel.PREFERENCE,
                    target_code=target,
                    ambiguity=ambiguity,
                )
            )
            if "没一起" in clause:
                interview.statements.append(
                    Statement(
                        StatementType.NO_JOINT_AGREEMENT,
                        clause,
                        record["id"],
                        consent=ConsentLevel.UNSTATED,
                    )
                )
            continue

        if "没一起" in clause:
            interview.statements.append(
                Statement(
                    StatementType.NO_JOINT_AGREEMENT,
                    clause,
                    record["id"],
                    consent=ConsentLevel.UNSTATED,
                )
            )
            continue

        if "有人接" in clause:
            # 提出接站条件，而非已接受某个接站人
            interview.statements.append(
                Statement(
                    StatementType.PICKUP_CONDITION,
                    clause,
                    record["id"],
                    consent=ConsentLevel.CONDITIONAL,
                )
            )
            continue

        if "别替我签" in clause or "先别" in clause:
            interview.statements.append(
                Statement(
                    StatementType.NO_PROXY_SIGN,
                    clause,
                    record["id"],
                    consent=ConsentLevel.UNSTATED,
                )
            )
            continue

        if "宿舍各管各" in clause or "自己解决" in clause or "住处由我" in clause:
            interview.statements.append(
                Statement(StatementType.DORMITORY_SELF, clause, record["id"])
            )
            continue

        if "白班可以" in clause:
            interview.statements.append(
                Statement(
                    StatementType.CONFIRM_WILLINGNESS,
                    clause,
                    record["id"],
                    consent=ConsentLevel.ACCEPTED,
                )
            )
            continue

        if "接受" in clause:
            job_id = None
            for name, jid in name_to_job.items():
                if name in clause:
                    job_id = jid
            station_match = re.search(r"到(东站|西站)", clause)
            target = _resolve_name(clause, persons)
            interview.statements.append(
                Statement(
                    StatementType.CONDITIONAL_JOB,
                    clause,
                    record["id"],
                    consent=ConsentLevel.CONDITIONAL,
                    job_id=job_id,
                    station=station_match.group(1) if station_match else None,
                    target_code=target,
                )
            )
            continue

        if "报到" in clause:
            # 报到时间属于岗位侧信息，面谈中出现只登记原话，不转成承诺
            interview.statements.append(
                Statement(StatementType.DORMITORY_OPEN, clause, record["id"])
            )
            continue

    return interview


# ---------------------------------------------------------------------------
# 岗位
# ---------------------------------------------------------------------------
def _parse_job(record: dict) -> Job:
    text = record["text"]
    job_id_match = _JOB_RE.search(text)
    job_id = job_id_match.group(0) if job_id_match else record["id"]
    station_match = re.search(r"(东站|西站)", text)
    station = station_match.group(1) if station_match else "东站"
    deadline_match = re.search(r"(\d{1,2}:\d{2})前", text)
    deadline = deadline_match.group(1) if deadline_match else "19:00"
    if "首日需晚班" in text:
        shift = ShiftType.FIRST_NIGHT
    elif "固定白班" in text:
        shift = ShiftType.FIXED_DAY
    else:
        shift = ShiftType.UNSTATED
    dormitory_promised = "不作为承诺" not in text and "宿舍" in text and "独立登记" not in text
    title = text.split("，")[0]
    return Job(
        job_id=job_id,
        title=title,
        station=station,
        deadline=deadline,
        shift=shift,
        dormitory_promised=dormitory_promised,
        source_id=record["id"],
        quote=text,
    )


# ---------------------------------------------------------------------------
# 接运与变更
# ---------------------------------------------------------------------------
def _parse_leg(record: dict) -> TransportLeg:
    text = record["text"]
    rid = record["id"]
    station_match = re.search(r"(东站|西站)", text)
    station = station_match.group(1) if station_match else "东站"
    times = _TIME_RE.findall(text)
    carrier_match = _CARRIER_RE.search(text)
    carrier = carrier_match.group(0) if carrier_match else ""
    receiver = _RECEIVER_RE.search(text)
    point_match = re.search(rf"集合点([{_CJK}]+)", text)
    seats = [
        Seat(person_code=code, confirmed=False, source_id=rid)
        for code in _PERSON_CODE_RE.findall(text)
    ]
    ticketed = "已出票" in text
    return TransportLeg(
        leg_id=rid,
        carrier=carrier,
        depart=times[0],
        arrive=times[1] if len(times) > 1 else times[0],
        station=station,
        status=LegStatus.TICKETED if ticketed else LegStatus.SCHEDULED,
        seats=seats,
        receiver_code=receiver.group(1) if receiver else None,
        pickup_point=point_match.group(1) if point_match else None,
        source_id=rid,
        quote=text,
    )


def _parse_change(record: dict) -> LegChange:
    text = record["text"]
    cancelled = tuple(re.findall(r"(C9\d)班次取消", text))
    replacements: list[dict] = []
    infeasible: list[dict] = []

    m = re.search(
        r"(C9\d)班次(?:未取消但)?故障换车，新班(\d{1,2}:\d{2})，(\d{1,2}:\d{2})到(东站|西站)，临时只剩(一|两|三)席",
        text,
    )
    if m:
        carrier, depart, arrive, station, seat_word = m.groups()
        seats_n = {"一": 1, "两": 2, "三": 3}[seat_word]
        replacements.append(
            {
                "carrier": carrier,
                "depart": depart,
                "arrive": arrive,
                "station": station,
                "seats": seats_n,
            }
        )

    m2 = re.search(r"另一班(\d{1,2}:\d{2})到(西站|东站)，西站到报到地点需一小时", text)
    if m2:
        depart, station = m2.groups()
        infeasible.append({"depart": depart, "station": station, "transfer_minutes": 60})

    return LegChange(
        source_id=record["id"],
        quote=text,
        cancelled=cancelled,
        replacements=tuple(replacements),
        infeasible=tuple(infeasible),
    )


# ---------------------------------------------------------------------------
# 本人确认
# ---------------------------------------------------------------------------
def _parse_confirmation(record: dict, persons: dict[str, Person]) -> Confirmation:
    text = record["text"]
    # 确认记录里可能只有姓名没有证件代号，先按姓名解析再回退代号
    present_codes = [
        person.code
        for person in persons.values()
        if person.name in text or person.code in text
    ]
    person = present_codes[0] if present_codes else ""
    companion = present_codes[1] if len(present_codes) > 1 else None
    station_match = re.search(r"到(东站|西站)", text)
    return Confirmation(
        source_id=record["id"],
        person_code=person,
        quote=text,
        companion_code=companion,
        station=station_match.group(1) if station_match else None,
        requires_receiver="没接站人不签" in text or "有人接" in text,
        west_must_reconfirm="西站" in text and "重新确认" in text,
    )


# ---------------------------------------------------------------------------
# 签认
# ---------------------------------------------------------------------------
def _parse_contract(
    record: dict,
    interviews: list[Interview],
    confirmations: list[Confirmation],
) -> Contract:
    text = record["text"]
    rid = record["id"]
    person = _PERSON_CODE_RE.search(text).group(0)
    job_id = _JOB_RE.search(text).group(0)

    # 本人同意强度：以面谈/确认为据，缺明确表态不得视为同意
    consent = ConsentLevel.UNSTATED
    for iv in interviews:
        if iv.person.code != person:
            continue
        for st in iv.statements:
            if st.type is StatementType.CONDITIONAL_JOB and st.job_id == job_id:
                consent = ConsentLevel.CONDITIONAL
            elif st.type is StatementType.CONFIRM_WILLINGNESS:
                consent = ConsentLevel.ACCEPTED
            elif st.type in (
                StatementType.PICKUP_CONDITION,
                StatementType.HARD_COMPANION,
            ):
                consent = ConsentLevel.CONDITIONAL
    if "未约定同车为条件" in text and consent is ConsentLevel.UNSTATED:
        consent = ConsentLevel.ACCEPTED

    conditions: list[EffectCondition] = []
    # 只有签认原话把同趟/同车写成生效条件时才登记；
    # "未约定同车为条件"是否定表述，面谈中的"最好同车"软偏好也不构成条件
    hard_companion_in_record = (
        ("同趟" in text or "同车" in text)
        and "未约定同车" not in text
        and "未约定同趟" not in text
    )
    if hard_companion_in_record:
        companion = None
        # 优先采用本人电话确认中明确的同行范围
        for conf in confirmations:
            if conf.person_code == person and conf.companion_code:
                companion = conf.companion_code
        # 其次取自本人面谈中的硬性条件指向；软偏好不得提升为合同条件
        if companion is None:
            for iv in interviews:
                if iv.person.code != person:
                    continue
                for st in iv.statements:
                    if st.type in (
                        StatementType.HARD_COMPANION,
                        StatementType.CONDITIONAL_JOB,
                    ) and st.target_code:
                        companion = st.target_code
        conditions.append(
            EffectCondition(
                code=ConditionCode.SAME_TRIP,
                detail="须与指定另一方同趟同车，且有匹配两席与对方确认的同行范围",
                source_id=rid,
                quote=text,
                companion_code=companion,
            )
        )
    if "东站有人接" in text or "有人接" in text:
        conditions.append(
            EffectCondition(
                code=ConditionCode.RECEIVER,
                detail="抵达东站时须有接站人",
                source_id=rid,
                quote=text,
                station="东站",
            )
        )
    if "抵达东站" in text or "到东站" in text:
        conditions.append(
            EffectCondition(
                code=ConditionCode.ARRIVE_STATION,
                detail="本人须抵达东站",
                source_id=rid,
                quote=text,
                station="东站",
            )
        )

    return Contract(
        contract_id=rid,
        person_code=person,
        job_id=job_id,
        source_id=rid,
        quote=text,
        consent=consent,
        conditions=conditions,
    )


# ---------------------------------------------------------------------------
# 领取（接站单）
# ---------------------------------------------------------------------------
def _parse_sheet(record: dict, legs: dict[str, TransportLeg]) -> PickupSheet:
    text = record["text"]
    rid = record["id"]
    carrier_match = _CARRIER_RE.search(text)
    carrier = carrier_match.group(0) if carrier_match else ""
    point_match = re.search(rf"集合点([{_CJK}]+)", text)
    receiver = _RECEIVER_RE.search(text)
    # 接站单关联的班次以来源明示的 references 为准
    leg_id = next(
        (ref for ref in record.get("references", []) if ref in legs), rid
    )
    return PickupSheet(
        sheet_id=f"{rid}-单",
        leg_id=leg_id,
        carrier=carrier,
        contact_codes=tuple(_CONTACT_RE.findall(text)),
        pickup_point=point_match.group(1) if point_match else None,
        receiver_code=receiver.group(1) if receiver else None,
        status=SheetStatus.CLAIMED,
        history=[StatusEvent(SheetStatus.CLAIMED, rid, "承运方领取接站单")],
        source_id=rid,
    )


# ---------------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------------
def parse_corpus(records: list[dict]) -> CaseFacts:
    facts = CaseFacts(raw_records=list(records))

    # 第一遍：收集人员
    for r in records:
        if r["kind"] != "面谈":
            continue
        m = _NAME_RE.match(r["text"])
        if m:
            name, code = m.groups()
            facts.persons[code] = Person(code=code, name=name, source_id=r["id"])

    # 第二遍先解析岗位与接运，使面谈中的岗位名、接站单的班次关联可解析
    for r in records:
        if r["kind"] == "岗位":
            job = _parse_job(r)
            facts.jobs[job.job_id] = job
        elif r["kind"] == "接运":
            leg = _parse_leg(r)
            facts.legs[leg.leg_id] = leg

    name_to_job = {
        keyword: job_id
        for job_id, job in facts.jobs.items()
        for keyword in re.findall(rf"[{_CJK}]+", job.title)
        if len(keyword) >= 2
    }

    # 第三遍：面谈、变更、确认（签认依赖面谈与确认，置后）
    for r in records:
        kind = r["kind"]
        if kind == "面谈":
            iv = _parse_interview(r, facts.persons, name_to_job)
            facts.interviews.append(iv)
            facts.ambiguities.extend(iv.ambiguities)
        elif kind == "接运变更":
            facts.changes.append(_parse_change(r))
        elif kind == "本人确认":
            facts.confirmations.append(_parse_confirmation(r, facts.persons))

    for r in records:
        if r["kind"] == "签认":
            facts.contracts.append(
                _parse_contract(r, facts.interviews, facts.confirmations)
            )
        elif r["kind"] == "领取":
            facts.sheets.append(_parse_sheet(r, facts.legs))

    return facts
