from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from services.roam_pets import capture, frames, maps, service, storage
from services.roam_pets.catalog import SPECIES, STARTERS

ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.TestCase):
    def test_six_species_unique_and_complete(self) -> None:
        self.assertEqual(len(SPECIES), 6)
        ids = list(SPECIES)
        self.assertEqual(len(ids), len(set(ids)))
        names = {s.name for s in SPECIES.values()}
        self.assertEqual(len(names), len(ids))
        for species in SPECIES.values():
            self.assertIn(species.element, ("火", "水", "草", "雷", "光", "暗"))
            self.assertIn(species.rarity, ("普通", "稀有", "史诗"))
            self.assertIn(species.temper, ("活泼", "温顺", "高傲", "胆小"))
            self.assertTrue(set(species.colors) >= {"body", "belly", "accent", "extra"})
            for color in species.colors.values():
                self.assertNotEqual(color.lower(), frames.TRANSPARENT_KEY)

    def test_starters_are_valid_species(self) -> None:
        self.assertEqual(set(STARTERS), set(STARTERS) & set(SPECIES))
        for sid in STARTERS:
            self.assertEqual(SPECIES[sid].rarity, "普通")


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = storage.blank()

    def test_adopt_registers_dex_and_sets_active(self) -> None:
        state = service.adopt(self.data, "emberpup", "小火")
        self.assertEqual(self.data["active"], "emberpup")
        self.assertEqual(self.data["dex"]["caught"], ["emberpup"])
        self.assertEqual(state["nickname"], "小火")

    def test_feed_caps_and_refuses_overfeed(self) -> None:
        service.adopt(self.data, "mosshell")
        self.data["pets"]["mosshell"]["hunger"] = 96
        with self.assertRaises(ValueError):
            service.feed(self.data, "mosshell")
        self.data["pets"]["mosshell"]["hunger"] = 50
        result = service.feed(self.data, "mosshell")
        self.assertEqual(result["state"]["hunger"], 72)

    def test_play_requires_energy(self) -> None:
        service.adopt(self.data, "dewfrog")
        self.data["pets"]["dewfrog"]["energy"] = 10
        with self.assertRaises(ValueError):
            service.play(self.data, "dewfrog")
        self.data["pets"]["dewfrog"]["energy"] = 60
        result = service.play(self.data, "dewfrog")
        self.assertEqual(result["state"]["energy"], 42)

    def test_tick_decays_by_appetite(self) -> None:
        service.adopt(self.data, "emberpup")
        self.data["pets"]["emberpup"]["hunger"] = 80
        self.data["pets"]["emberpup"]["last_seen"] = (
            datetime.now().astimezone() - timedelta(hours=2)
        ).isoformat()
        self.data = service.tick(self.data, now=datetime.now().astimezone())
        self.assertAlmostEqual(self.data["pets"]["emberpup"]["hunger"], 80 - 1.8, delta=0.6)

    def test_level_up_event(self) -> None:
        service.adopt(self.data, "voltfinch")
        self.data["pets"]["voltfinch"]["exp"] = 9
        result = service.feed(self.data, "voltfinch")
        self.assertTrue(any("Lv.2" in event for event in result["events"]))

    def test_dex_view_unknown_is_silhouetted(self) -> None:
        service.adopt(self.data, "emberpup")
        rows = {row["id"]: row for row in service.dex_view(self.data)}
        self.assertEqual(rows["emberpup"]["status"], "caught")
        self.assertNotEqual(rows["umbrae"]["status"], "caught")
        self.assertEqual(rows["umbrae"]["name"], "？？？")
        self.assertEqual(rows["umbrae"]["flavor"], "——")


class StorageTests(unittest.TestCase):
    def test_roundtrip_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roam_pets.json"
            data = storage.blank()
            service.adopt(data, "lunebunny", "月亮")
            storage.save(data, path)
            loaded = storage.load(path)
            self.assertEqual(loaded["active"], "lunebunny")
            self.assertEqual(loaded["pets"]["lunebunny"]["nickname"], "月亮")
            leftovers = [p for p in Path(directory).iterdir() if p.name.startswith(".roam_")]
            self.assertEqual(leftovers, [])

    def test_corrupt_file_returns_blank(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roam_pets.json"
            path.write_text("{broken", encoding="utf-8")
            data = storage.load(path)
            self.assertEqual(data["pets"], {})


class CaptureTests(unittest.TestCase):
    def test_ball_ordering_and_hp_effect(self) -> None:
        p_basic = capture.catch_probability("basic", "umbrae", hp_ratio=1.0)
        p_great = capture.catch_probability("great", "umbrae", hp_ratio=1.0)
        p_ultra_low = capture.catch_probability("ultra", "umbrae", hp_ratio=0.2)
        self.assertGreater(p_great, p_basic)
        self.assertGreater(p_ultra_low, p_great)

    def test_probability_bounds(self) -> None:
        for species_id in SPECIES:
            for ball in capture.BALLS:
                p = capture.catch_probability(ball, species_id, hp_ratio=0.05)
                self.assertTrue(0.05 <= p <= 0.95, (species_id, ball, p))

    def test_attempt_reports_shakes(self) -> None:
        result = capture.attempt("ultra", "emberpup", hp_ratio=0.1, rng=__import__("random").Random(42))
        self.assertEqual(len(result["shakes"]), 3)
        self.assertEqual(result["caught"], all(result["shakes"]))


class MapTests(unittest.TestCase):
    def test_only_plains_unlocked_at_level_one(self) -> None:
        unlocked = [scene.id for scene in maps.unlocked_maps(1)]
        self.assertEqual(unlocked, ["plains"])

    def test_encounters_reference_real_species(self) -> None:
        for scene in maps.MAPS.values():
            for species_id, weight in scene.encounters:
                self.assertIn(species_id, SPECIES)
                self.assertGreater(weight, 0)


class FrameTests(unittest.TestCase):
    def test_frame_contains_body_and_eyes(self) -> None:
        species = SPECIES["emberpup"]
        shapes = frames.build_frame(species, 0.0, "开心")
        kinds = [item["kind"] for item in shapes]
        self.assertIn("oval", kinds)
        ovals = [item for item in shapes if item["kind"] == "oval"]
        self.assertGreaterEqual(len(ovals), 7)

    def test_blink_changes_eye_rendering(self) -> None:
        species = SPECIES["lunebunny"]
        open_frame = frames.build_frame(species, 1.0, "开心")
        blink_frame = frames.build_frame(species, 3.71, "开心")
        # 睁眼帧：眼睛是深色椭圆；眨眼帧：眼睛变成线条
        open_eye_ovals = sum(
            1 for item in open_frame
            if item["kind"] == "oval" and item["fill"] == species.colors["extra"]
        )
        self.assertGreater(open_eye_ovals, 0)
        blink_eyes = sum(1 for item in blink_frame if item["kind"] == "line")
        self.assertGreaterEqual(blink_eyes, 2)

    def test_silhouette_is_uniform_dark(self) -> None:
        shapes = frames.silhouette(SPECIES["umbrae"])
        for item in shapes:
            self.assertEqual(item["fill"], frames.SILHOUETTE_COLOR)

    def test_palette_never_uses_transparent_key(self) -> None:
        for species in SPECIES.values():
            shapes = frames.build_frame(species, 0.5, "平静")
            for item in shapes:
                for key in ("fill", "outline"):
                    value = item.get(key, "")
                    if isinstance(value, str):
                        self.assertNotEqual(value.lower(), frames.TRANSPARENT_KEY)


class WebUITests(unittest.TestCase):
    def test_pets_page_flow(self) -> None:
        script = r'''
import re
from fastapi.testclient import TestClient
from web.app import app
from core.database import init_db
init_db()
c = TestClient(app)
page = c.get("/pets").text
assert "宠物乐园" in page and "初始三选一" in page and "宠物图鉴" in page and "捕捉与地图" in page
r = c.post("/pets/adopt", data={"species_id": "dewfrog", "nickname": "小露"})
assert "小露 加入了你的研究旅程" in r.text, r.text[:300]
page = c.get("/pets").text
assert "跟随我" not in page or "roam-following" in page
assert "已捕获 1" in page
r = c.post("/pets/feed", data={"species_id": "dewfrog"})
assert "吃得很香" in r.text, r.text[:300]
r = c.post("/pets/play", data={"species_id": "dewfrog"})
assert "玩得很开心" in r.text
r = c.post("/pets/adopt", data={"species_id": "umbrae", "nickname": "影"})
assert "尚未开放获取" in r.text
print("roam web flow pass")
'''
        with tempfile.TemporaryDirectory(prefix="roam-ui-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=ROOT, env=env,
                capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
