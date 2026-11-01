from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_haze_trim_clears_near_transparent_pixels(self) -> None:
        frame = Image.new("RGBA", (4, 1))
        frame.putdata([(0, 0, 0, 0), (0, 0, 0, 7), (0, 0, 0, 8), (0, 0, 0, 200)])
        cleaned = pet._trim_haze(frame)
        self.assertEqual(
            [cleaned.getpixel((x, 0))[3] for x in range(4)], [0, 0, 8, 200]
        )

    def test_transparent_key_never_matches_the_art(self) -> None:
        key = tuple(int(pet.TRANSPARENT_KEY[i : i + 2], 16) for i in (1, 3, 5))
        image = Image.open(pet._asset_path()).convert("RGBA")
        for r, g, b, alpha in image.getdata():
            if alpha > 0 and (r, g, b) == key:
                self.fail(f"透明键 {key} 与灵知本体像素重合，会在角色上挖洞")

    def test_form_tint_preserves_alpha(self) -> None:
        original = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        original.paste(Image.new("RGBA", (40, 40), (200, 200, 200, 255)), (30, 30))
        tinted = pet._form_tinted(original, {"tint": (200, 120, 60), "alpha": 0.2})
        self.assertEqual(tinted.getpixel((0, 0))[3], 0)
        self.assertEqual(tinted.getpixel((50, 50))[3], 255)
        self.assertNotEqual(tinted.getpixel((50, 50)), original.getpixel((50, 50)))

    def test_sprout_form_keeps_original_colours(self) -> None:
        original = Image.new("RGBA", (20, 20), (120, 180, 140, 255))
        same = pet._form_tinted(original, {"tint": (110, 170, 120), "alpha": 0.0})
        self.assertEqual(same.getpixel((10, 10)), original.getpixel((10, 10)))

    def test_save_settings_is_atomic_and_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "desktop_pet.json"
            with patch.object(pet, "SETTINGS_PATH", path), patch.object(
                pet, "USER_CONFIG_DIR", Path(directory)
            ):
                pet._save_settings({"progression": {"xp": 3}, "pats": 2})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"progression": {"xp": 3}, "pats": 2},
            )
            self.assertFalse((Path(directory) / "desktop_pet.json.tmp").exists())

    def test_corrupt_settings_fall_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "desktop_pet.json"
            path.write_text("{not json", encoding="utf-8")
            with patch.object(pet, "SETTINGS_PATH", path):
                self.assertEqual(pet._load_settings(), {})

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
