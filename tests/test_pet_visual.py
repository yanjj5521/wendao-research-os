from __future__ import annotations

import unittest
from pathlib import Path

from PIL import Image

import desktop_pet as pet


class PetVisualTests(unittest.TestCase):
    def test_tint_keeps_transparent_corners(self) -> None:
        original = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        original.paste(Image.new("RGBA", (40, 40), (200, 100, 50, 255)), (30, 30))
        tinted = pet._tinted(original, "proud")
        self.assertEqual(tinted.getpixel((0, 0))[3], 0)
        self.assertEqual(tinted.getpixel((50, 50))[3], 255)

    def test_real_asset_keeps_transparent_corners(self) -> None:
        original = Image.open(pet._asset_path()).convert("RGBA").resize((120, 120))
        frame = pet._tinted(original, "proud")
        self.assertEqual(frame.getpixel((0, 0))[3], 0)
        self.assertEqual(frame.getpixel((119, 119))[3], 0)

    def test_pet_has_no_painted_background_layer(self) -> None:
        source = Path(pet.__file__).read_text(encoding="utf-8")
        for marker in ("_aura_layer", "AURA_TINT", "_compose_frame", "PET_PADDING"):
            self.assertNotIn(marker, source)

    def test_clamp_position_keeps_window_on_screen(self) -> None:
        self.assertEqual(pet._clamp_position(-40, -10, 200, 200, 300, 300), (0, 0))
        self.assertEqual(pet._clamp_position(500, 500, 200, 200, 300, 300), (100, 100))
        self.assertEqual(pet._clamp_position(60, 70, 200, 200, 300, 300), (60, 70))


if __name__ == "__main__":
    unittest.main()
