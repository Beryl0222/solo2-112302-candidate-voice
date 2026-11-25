"""意愿解释规则：原话保留、指代留核实、同意不被推定。"""
import unittest

from employment_station import build_casebook
from employment_station.models import (
    ConsentLevel,
    SheetStatus,
    StatementType,
)


class InterpretationTest(unittest.TestCase):
    def setUp(self):
        self.book = build_casebook()
        self.facts = self.book.facts

    def test_original_words_are_kept_alongside_structured_facts(self):
        for iv in self.facts.interviews:
            self.assertTrue(iv.original_text.strip())
            for st in iv.statements:
                self.assertIn(st.quote, iv.original_text)

    def test_night_shift_question_is_about_another_person_not_self_consent(self):
        a1 = next(iv for iv in self.facts.interviews if iv.person.code == "C11")
        referrals = [
            st for st in a1.statements if st.type is StatementType.NIGHT_SHIFT_REFERRAL
        ]
        self.assertEqual(len(referrals), 1)
        # 问的是秋禾，不能记成林予本人同意上夜班
        self.assertIsNot(referrals[0].consent, ConsentLevel.ACCEPTED)
        self.assertTrue(a1.ambiguities)

    def test_companion_preference_is_soft_not_mutual_agreement(self):
        a1 = next(iv for iv in self.facts.interviews if iv.person.code == "C11")
        prefs = [
            st for st in a1.statements if st.type is StatementType.COMPANION_PREFERENCE
        ]
        self.assertTrue(prefs)
        self.assertEqual(prefs[0].consent, ConsentLevel.PREFERENCE)
        self.assertEqual(prefs[0].target_code, "C12")

    def test_silence_is_not_consent_and_proxy_signing_is_forbidden(self):
        a2 = next(iv for iv in self.facts.interviews if iv.person.code == "C12")
        no_proxy = [st for st in a2.statements if st.type is StatementType.NO_PROXY_SIGN]
        self.assertTrue(no_proxy)
        no_joint = [
            st for st in a2.statements if st.type is StatementType.NO_JOINT_AGREEMENT
        ]
        self.assertTrue(no_joint)
        # 任何陈述都不得在本人未明确时升级为已接受
        for st in a2.statements:
            self.assertIsNot(st.consent, ConsentLevel.REJECTED)

    def test_pickup_sentence_is_a_condition_not_accepted_receiver(self):
        a2 = next(iv for iv in self.facts.interviews if iv.person.code == "C12")
        pickup = [st for st in a2.statements if st.type is StatementType.PICKUP_CONDITION]
        self.assertEqual(len(pickup), 1)
        self.assertEqual(pickup[0].consent, ConsentLevel.CONDITIONAL)
        self.assertIsNone(pickup[0].target_code)  # 未接受任何具体接站人

    def test_ambiguous_referral_left_to_caseworker_but_other_conditions_remain_usable(self):
        a2 = next(iv for iv in self.facts.interviews if iv.person.code == "C12")
        ambiguous = [
            st
            for st in a2.statements
            if st.type is StatementType.HARD_COMPANION and st.ambiguity is not None
        ]
        self.assertTrue(ambiguous)
        self.assertIsNone(ambiguous[0].target_code)
        self.assertGreaterEqual(len(ambiguous[0].ambiguity.interpretations), 2)
        # 同场面谈的接站条件仍明确、仍可用于撮合
        self.assertTrue(
            any(st.type is StatementType.PICKUP_CONDITION for st in a2.statements)
        )

    def test_later_confirmation_resolves_companion_scope_to_c13(self):
        confirm = next(c for c in self.facts.confirmations if c.person_code == "C12")
        self.assertEqual(confirm.companion_code, "C13")
        self.assertTrue(confirm.requires_receiver)
        self.assertTrue(confirm.west_must_reconfirm)

    def test_first_day_shift_is_night_despite_later_day_shift_option(self):
        j3 = self.facts.jobs["J3"]
        self.assertEqual(j3.shift.value, "首日晚班")
        self.assertIn("可申请转白班", j3.quote)

    def test_dormitory_words_never_become_employer_promise(self):
        self.assertFalse(self.facts.jobs["J1"].dormitory_promised)
        self.assertFalse(self.facts.jobs["J2"].dormitory_promised)


if __name__ == "__main__":
    unittest.main()
