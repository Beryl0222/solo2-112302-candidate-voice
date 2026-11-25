"""接站单生命周期与接站方交接包最小化。"""
import unittest

from employment_station import build_casebook
from employment_station.models import SheetStatus


class HandoverTest(unittest.TestCase):
    def setUp(self):
        self.book = build_casebook()
        self.sheets = {s.sheet_id: s for s in self.book.sheets()}

    def test_old_sheet_statuses_remain_queryable(self):
        m1 = self.sheets["M1-单"]
        self.assertEqual(m1.status, SheetStatus.WITHDRAWN)
        self.assertTrue(
            any(e.status is SheetStatus.WITHDRAWN for e in m1.history)
        )
        m2 = self.sheets["M2-单"]
        self.assertEqual(m2.status, SheetStatus.SUPERSEDED)

    def test_reissued_sheet_does_not_carry_old_pickup_point(self):
        new_sheet = self.sheets["M2-单-换发"]
        self.assertEqual(new_sheet.status, SheetStatus.REISSUED)
        self.assertIsNone(new_sheet.pickup_point)  # 旧"桥口"不得出现
        # 旧单仍能查到原集合点，仅用于驻点留档
        self.assertEqual(self.sheets["M2-单"].pickup_point, "桥口")

    def test_handover_packet_contains_only_segment_contact_fields(self):
        packet = self.book.handover_packet("M2-单-换发").as_dict()
        self.assertEqual(
            set(packet), {"联络码", "发车时间", "上车点", "接站人"}
        )
        self.assertEqual(packet["联络码"], ["X11"])
        self.assertIn("待核实", packet["上车点"])
        # 岗位编号、家庭原话等不得出现在交接包
        flat = str(packet)
        for forbidden in ("J1", "J2", "J3", "宿舍", "同车", "林予", "秋禾", "乐林", "桥口"):
            self.assertNotIn(forbidden, flat)

    def test_withdrawn_or_superseded_sheets_cannot_be_handed_over(self):
        for sheet_id in ("M1-单", "M2-单"):
            with self.assertRaises(ValueError):
                self.book.handover_packet(sheet_id)


if __name__ == "__main__":
    unittest.main()
