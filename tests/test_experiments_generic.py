from __future__ import annotations

import sqlite3
import unittest

from core.migrations import _genericize_experiment_ledger


SCHEMA = """
CREATE TABLE workspaces(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);
CREATE TABLE experiments(
    id INTEGER PRIMARY KEY,
    conditions TEXT NOT NULL DEFAULT '',
    measurements TEXT NOT NULL DEFAULT '',
    eg_content REAL,
    water_cement_ratio REAL,
    compaction_pressure REAL,
    thickness_cm REAL,
    area_cm2 REAL,
    electrolyte TEXT NOT NULL DEFAULT '',
    voltage_min REAL,
    voltage_max REAL,
    scan_rate REAL,
    specific_capacitance REAL,
    conductivity REAL,
    compressive_strength REAL
);
"""


class GenericLedgerMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(SCHEMA)

    def tearDown(self) -> None:
        self.conn.close()

    def test_legacy_values_move_into_generic_text(self) -> None:
        self.conn.executescript(
            """
            INSERT INTO experiments(id, eg_content, electrolyte, specific_capacitance)
            VALUES (1, 6, '6 M KOH', 120);
            INSERT INTO experiments(id, conditions, eg_content) VALUES (2, '手填条件', 9);
            """
        )
        _genericize_experiment_ledger(self.conn)
        conditions, measurements = self.conn.execute(
            "SELECT conditions, measurements FROM experiments WHERE id=1"
        ).fetchone()
        self.assertIn("EG 含量 6", conditions)
        self.assertIn("电解液 6 M KOH", conditions)
        self.assertIn("比电容 120", measurements)
        kept = self.conn.execute(
            "SELECT conditions FROM experiments WHERE id=2"
        ).fetchone()[0]
        self.assertEqual(kept, "手填条件", "已有通用文本不得被覆盖")

    def test_only_factory_workspace_is_renamed(self) -> None:
        self.conn.executescript(
            """
            INSERT INTO workspaces(id, name, description) VALUES (1, 'EG 实验', '旧描述');
            INSERT INTO workspaces(id, name, description) VALUES (2, '我的电化学', '自定义');
            """
        )
        _genericize_experiment_ledger(self.conn)
        names = dict(self.conn.execute("SELECT id, name FROM workspaces"))
        self.assertEqual(names[1], "通用DIY实验")
        self.assertEqual(names[2], "我的电化学", "用户自定义名称必须保留")


if __name__ == "__main__":
    unittest.main()
