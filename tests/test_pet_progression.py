from __future__ import annotations

import unittest

from services.pet_progression import (
    ACTIVITY_CAP,
    MISSION_CAP,
    PAT_CAP,
    award,
    describe,
    form_for_level,
    form_gallery,
    level_for_xp,
    load_progress,
    record_pat,
    rules_text,
    xp_for_level,
)


class LevelCurveTests(unittest.TestCase):
    def test_thresholds(self) -> None:
        self.assertEqual(xp_for_level(1), 0)
        self.assertEqual(xp_for_level(2), 50)
        self.assertEqual(xp_for_level(3), 150)
        self.assertEqual(xp_for_level(4), 300)
        self.assertEqual(level_for_xp(49), 1)
        self.assertEqual(level_for_xp(50), 2)
        self.assertEqual(level_for_xp(149), 2)
        self.assertEqual(level_for_xp(150), 3)
        self.assertEqual(level_for_xp(10**9), 20)

    def test_forms_unlock_at_expected_levels(self) -> None:
        self.assertEqual(form_for_level(1)["key"], "sprout")
        self.assertEqual(form_for_level(2)["key"], "sprout")
        self.assertEqual(form_for_level(3)["key"], "leaf")
        self.assertEqual(form_for_level(5)["key"], "flower")
        self.assertEqual(form_for_level(8)["key"], "fruit")
        self.assertEqual(form_for_level(12)["key"], "star")
        self.assertEqual(form_for_level(20)["key"], "star")


class ProgressStateTests(unittest.TestCase):
    def test_load_missing_state_starts_at_level_one(self) -> None:
        progress = load_progress(None, "2026-11-01")
        self.assertEqual(progress.level, 1)
        self.assertEqual(progress.xp, 0)
        self.assertEqual(progress.form, "sprout")
        self.assertEqual(progress.day, "2026-11-01")

    def test_day_change_resets_daily_counters_but_keeps_xp(self) -> None:
        progress = load_progress(
            {"xp": 160, "day": "2026-10-31", "today": {"wake": True, "pat_xp": 3}},
            "2026-11-01",
        )
        self.assertEqual(progress.xp, 160)
        self.assertEqual(progress.level, 3)
        self.assertFalse(progress.today["wake"])
        self.assertEqual(progress.today["pat_xp"], 0)

    def test_round_trip(self) -> None:
        progress = load_progress({"xp": 120}, "2026-11-01")
        restored = load_progress(progress.to_dict(), "2026-11-01")
        self.assertEqual(restored.xp, 120)
        self.assertEqual(restored.level, 2)


class AwardTests(unittest.TestCase):
    def test_bootstrap_marks_history_seen_but_still_wakes(self) -> None:
        progress = load_progress(None, "2026-11-01")
        progress, events = award(
            progress,
            day="2026-11-01",
            activity_ids=[1, 2, 3, 4],
            missions_done=2,
            bootstrap=True,
        )
        self.assertEqual(progress.xp, 10)
        self.assertEqual(progress.seen_activity_id, 4)
        self.assertEqual(progress.today["seen_missions"], 2)
        self.assertEqual([event["type"] for event in events], ["xp"])

    def test_new_activity_awards_once_and_caps(self) -> None:
        progress = load_progress(None, "2026-11-01")
        progress, _ = award(progress, day="2026-11-01", activity_ids=[], missions_done=0)
        start = progress.xp
        progress, _ = award(progress, day="2026-11-01", activity_ids=[1, 2], missions_done=0)
        self.assertEqual(progress.xp, start + 4)
        progress, _ = award(progress, day="2026-11-01", activity_ids=[1, 2], missions_done=0)
        self.assertEqual(progress.xp, start + 4)
        progress, _ = award(
            progress, day="2026-11-01", activity_ids=list(range(1, 60)), missions_done=0
        )
        self.assertEqual(progress.today["activity_xp"], ACTIVITY_CAP)

    def test_missions_award_delta_and_cap(self) -> None:
        progress = load_progress(None, "2026-11-01")
        progress, _ = award(progress, day="2026-11-01", missions_done=2)
        self.assertEqual(progress.today["mission_xp"], 16)
        progress, _ = award(progress, day="2026-11-01", missions_done=2)
        self.assertEqual(progress.today["mission_xp"], 16)
        progress, _ = award(progress, day="2026-11-01", missions_done=10)
        self.assertEqual(progress.today["mission_xp"], MISSION_CAP)

    def test_pat_xp_is_capped(self) -> None:
        progress = load_progress(None, "2026-11-01")
        for _ in range(10):
            progress, _ = record_pat(progress, day="2026-11-01")
        self.assertEqual(progress.today["pat_xp"], PAT_CAP)

    def test_level_up_emits_unlock_event(self) -> None:
        progress = load_progress({"xp": 145}, "2026-11-01")
        progress, events = award(
            progress, day="2026-11-01", activity_ids=[], missions_done=0
        )
        self.assertEqual(progress.level, 3)
        unlock = [event for event in events if event["type"] == "level"]
        self.assertEqual(unlock[-1]["form"], "leaf")
        self.assertTrue(unlock[-1]["unlocked"])

    def test_describe_reports_progress_and_next_form(self) -> None:
        progress = load_progress({"xp": 160}, "2026-11-01")
        summary = describe(progress)
        self.assertEqual(summary["level"], 3)
        self.assertEqual(summary["next_form"]["key"], "flower")
        self.assertGreater(summary["percent"], 0)
        self.assertLess(summary["percent"], 1)

    def test_rules_are_listed(self) -> None:
        text = "\n".join(rules_text())
        self.assertIn("唤醒", text)
        self.assertIn("形态", text)

    def test_form_gallery_marks_current_and_locked(self) -> None:
        gallery = form_gallery(5)
        by_key = {item["key"]: item for item in gallery}
        self.assertTrue(by_key["flower"]["current"])
        self.assertTrue(by_key["leaf"]["unlocked"])
        self.assertFalse(by_key["fruit"]["unlocked"])
        self.assertFalse(by_key["star"]["unlocked"])


if __name__ == "__main__":
    unittest.main()
