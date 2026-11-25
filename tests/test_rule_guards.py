"""规则兜底：用额外合成记录验证引擎不依赖语料中的既有结论。"""
import copy
import unittest

from employment_station import build_casebook
from employment_station.models import ContractStatus, LegStatus

BASE = {
    "project": "合成规则演练",
    "material_nature": "本地合成演练资料",
}


def _record(rid, kind, order, text, refs=None):
    return {
        "id": rid,
        "kind": kind,
        "text": text,
        "arrival_order": order,
        "references": refs or [],
    }


class MissingConsentTest(unittest.TestCase):
    def _records_with_silent_worker(self):
        records = [
            _record("A9", "面谈", 1, "沈默，证件代号C19：我先看看，没说不去。"),
            _record("J9", "岗位", 2, "西站包装，固定白班，19:00前抵达。住宿独立登记。"),
            # 登记员越权直接送签：本人从未明确同意
            _record("R9", "签认", 3, "雇主与C19确认J9。", ["A9", "J9"]),
        ]
        return records

    def test_silence_never_becomes_signable_contract(self):
        book = build_casebook(records=copy.deepcopy(self._records_with_silent_worker()))
        r9 = next(c for c in book.facts.contracts if c.contract_id == "R9")
        self.assertEqual(r9.status, ContractStatus.BLOCKED)
        self.assertEqual(book.signable_jobs(), [])
        self.assertTrue(any("本人明确同意" in line for line in r9.condition_results))

    def test_unconfirmed_pair_does_not_consume_protected_seats(self):
        # 已出票一席给 C21；C22/C23 自称同车但无本人确认、无合同条件
        records = [
            _record("A21", "面谈", 1, "周正，证件代号C21：到东站就行。"),
            _record("A22", "面谈", 2, "吴凭，证件代号C22：我跟C23同车最好。"),
            _record("A23", "面谈", 3, "郑好，证件代号C23：我跟C22同车最好。"),
            _record("J21", "岗位", 4, "东站库房，东站报到截至19:00，固定白班，宿舍不作为承诺。"),
            _record("V9", "接运", 5, "东站17:40 C92承运，C21已出票，预计18:55到站。"),
            _record(
                "V9C",
                "接运变更",
                6,
                "C92班次故障换车，新班18:10，18:58到东站，临时只剩一席。",
                ["V9"],
            ),
        ]
        book = build_casebook(records=records)
        leg = book.facts.legs["V9-NEW"]
        self.assertEqual(leg.status, LegStatus.TICKETED)
        self.assertEqual([s.person_code for s in leg.seats], ["C21"])
        # 软偏好组合不产生硬同行拒绝记录，也不占用硬承诺席位
        alloc = next(a for a in book.allocations if a.leg_id == "V9-NEW")
        self.assertEqual(alloc.protected, ("C21",))
        self.assertEqual(alloc.rejected_pairs, ())


if __name__ == "__main__":
    unittest.main()
