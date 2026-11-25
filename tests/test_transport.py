"""运输变更、席位保护与硬同行不拆开。"""
import unittest

from employment_station import build_casebook
from employment_station.models import LegStatus, to_minutes


class TransportTest(unittest.TestCase):
    def setUp(self):
        self.book = build_casebook()
        self.legs = self.book.facts.legs

    def test_cancelled_and_replaced_legs_keep_old_records_queryable(self):
        self.assertEqual(self.legs["V1"].status, LegStatus.CANCELLED)
        self.assertEqual(self.legs["V2"].status, LegStatus.REPLACED)
        new_leg = self.legs["V2-NEW"]
        self.assertEqual(new_leg.supersedes, "V2")
        # 原话保留
        self.assertIn("故障换车", new_leg.quote)

    def test_replacement_arrives_within_reporting_deadline(self):
        new_leg = self.legs["V2-NEW"]
        self.assertLessEqual(to_minutes(new_leg.arrive), to_minutes("19:00"))

    def test_west_alternative_is_infeasible_after_transfer_time(self):
        west = [leg for leg in self.legs.values() if leg.station == "西站"]
        self.assertTrue(west)
        for leg in west:
            self.assertEqual(leg.status, LegStatus.INFEASIBLE)

    def test_ticketed_hard_promise_seat_is_protected(self):
        alloc = next(a for a in self.book.allocations if a.leg_id == "V2-NEW")
        self.assertIn("C11", alloc.protected)

    def test_hard_tied_pair_is_not_split_over_single_seat(self):
        alloc = next(a for a in self.book.allocations if a.leg_id == "V2-NEW")
        rejected = [pair for pair in alloc.rejected_pairs if set(pair) == {"C12", "C13"}]
        self.assertTrue(rejected)
        new_leg = self.legs["V2-NEW"]
        codes = {s.person_code for s in new_leg.seats}
        # 只剩一席：C12/C13 都不得被单独塞进去凑录用数
        self.assertNotIn("C12", codes)
        self.assertNotIn("C13", codes)
        self.assertEqual(new_leg.capacity, 1)

    def test_change_cannot_cancel_still_valid_independent_contract(self):
        # 运输调整不改变岗位合同本身的可签状态（R1 在合同测试中验证可签）
        r1 = next(c for c in self.book.facts.contracts if c.contract_id == "R1")
        self.assertIn("V2-NEW", r1.dependent_legs)


if __name__ == "__main__":
    unittest.main()
