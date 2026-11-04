from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProfessionTests(unittest.TestCase):
    """修仙百艺：经验、等阶、加成与页面。"""

    def test_profession_tiers_and_page(self) -> None:
        script = r'''
from fastapi.testclient import TestClient
from web.app import app
from core.database import init_db
from services import professions

init_db()
assert professions.active() == "alchemist"

# 经验 → 等阶：120 是入门门槛
add = professions.add_exp("alchemist", 120)
assert add["leveled"] is True and add["tier"] == "入门", add
assert round(professions.bonus("alchemist"), 2) == 1.08
# 加成会抬高成功率，但不会到 100%
assert 0.78 < professions.success_chance("alchemist", 0.78) < 0.98

add2 = professions.add_exp("beastmaster", 4800)
assert add2["tier"] == "天工", add2
assert professions.success_chance("beastmaster", 0.66) <= 0.98

state = professions.state()
assert len(state["cards"]) == 3
names = [card["name"] for card in state["cards"]]
assert names == ["炼丹师", "炼器师", "御兽师"], names

client = TestClient(app)
page = client.get("/professions")
assert page.status_code == 200
for marker in ("炼丹师", "炼器师", "御兽师", "六阶等阶", "设为主修", "当前主修"):
    assert marker in page.text, marker

switch = client.post("/professions/artificer/activate", follow_redirects=True)
assert switch.status_code == 200
assert professions.active() == "artificer"
print("professions pass")
'''
        with tempfile.TemporaryDirectory(prefix="prof-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("professions pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
