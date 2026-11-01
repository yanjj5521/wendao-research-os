from __future__ import annotations

import unittest

from hub.db import ASSET_KEYS
from hub.social import (
    SOCIAL_ASSET_GROUPS,
    SOCIAL_ASSET_LABELS,
    SOCIAL_TRANSFER_LIMITS,
    social_amount,
    social_asset,
)


class SocialAssetTests(unittest.TestCase):
    def test_categories_cover_every_asset(self) -> None:
        categories = {name for name, _ in SOCIAL_ASSET_GROUPS}
        self.assertTrue({"基础", "灵草", "法宝", "天地灵物"} <= categories)
        grouped = [key for _, items in SOCIAL_ASSET_GROUPS for key, _ in items]
        self.assertEqual(set(grouped), set(ASSET_KEYS))
        self.assertEqual(len(grouped), len(set(grouped)))

    def test_every_asset_has_label_and_limit(self) -> None:
        for key in ASSET_KEYS:
            self.assertIn(key, SOCIAL_ASSET_LABELS)
            self.assertIn(key, SOCIAL_TRANSFER_LIMITS)
            self.assertGreater(SOCIAL_TRANSFER_LIMITS[key], 0)

    def test_new_material_can_be_traded(self) -> None:
        for key in ("qi_grass", "azure_sword", "primordial_stone"):
            self.assertEqual(social_asset(key), key)
            self.assertEqual(social_amount(key, 1), 1)


if __name__ == "__main__":
    unittest.main()
