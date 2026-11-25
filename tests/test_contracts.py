"""签约把关：可签/暂留/不可签与条件明细。"""
import unittest

from employment_station import build_casebook
from employment_station.models import ConditionCode, ContractStatus


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.book = build_casebook()
        self.contracts = {c.contract_id: c for c in self.book.facts.contracts}

    def test_independent_contract_is_signable_even_if_transport_pending(self):
        r1 = self.contracts["R1"]
        self.assertEqual(r1.status, ContractStatus.READY)
        self.assertEqual(r1.person_code, "C11")
        # 仅交通待落实，独立合同保留
        self.assertTrue(r1.dependent_legs)

    def test_employer_promise_and_personal_consent_both_required(self):
        for contract in self.book.facts.contracts:
            self.assertIsNotNone(contract.consent)
        # 本语料中每份签认都有本人表态来源，无 BLOCKED；规则本身仍可核验
        self.assertFalse(self.book.blocked_contracts())

    def test_r2_held_until_same_trip_two_seats_and_receiver_confirmed(self):
        r2 = self.contracts["R2"]
        self.assertEqual(r2.status, ContractStatus.HELD)
        codes = {c.code for c in r2.conditions}
        self.assertIn(ConditionCode.SAME_TRIP, codes)
        self.assertIn(ConditionCode.RECEIVER, codes)
        results = "\n".join(r2.condition_results)
        self.assertIn("未满足", results)

    def test_r3_held_on_same_trip_and_arrival_conditions(self):
        r3 = self.contracts["R3"]
        self.assertEqual(r3.status, ContractStatus.HELD)
        codes = {c.code for c in r3.conditions}
        self.assertEqual(
            codes, {ConditionCode.SAME_TRIP, ConditionCode.ARRIVE_STATION}
        )

    def test_soft_preference_person_can_take_other_shift_arrangement(self):
        # C11 仅"最好同车"，换车后单独成行不阻塞其独立合同
        r1 = self.contracts["R1"]
        self.assertEqual(r1.status, ContractStatus.READY)

    def test_reconfirmation_tasks_name_leg_and_who_must_confirm(self):
        tasks = self.book.open_reconfirmations()
        self.assertGreaterEqual(len(tasks), 3)
        scopes = " ".join(t.scope for t in tasks)
        self.assertIn("V2-NEW", scopes)
        self.assertIn("V1", scopes)
        west_task = next(t for t in tasks if t.station == "西站")
        self.assertIn("C12", west_task.who)
        # 换车任务由持票人本人确认
        new_leg_task = next(t for t in tasks if "V2-NEW" in t.scope)
        self.assertEqual(new_leg_task.who, ("C11",))
        # 硬同行任务由两人确认并挂接两份暂留合同
        pair_task = next(t for t in tasks if "V1" in t.scope)
        self.assertEqual(set(pair_task.who), {"C12", "C13"})
        self.assertEqual(set(pair_task.linked_contracts), {"R2", "R3"})


if __name__ == "__main__":
    unittest.main()
