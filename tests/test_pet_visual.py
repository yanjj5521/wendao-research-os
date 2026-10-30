from __future__ import annotations

import unittest

from PIL import Image

import desktop_pet as pet


class PetVisualTests(unittest.TestCase):
    def test_aura_layer_has_padding_and_is_cached(self) -> None:
        aura = pet._aura_layer(120, 130, "calm")
        self.assertEqual(aura.size, (120 + pet.PET_PADDING * 2, 130 + pet.PET_PADDING * 2))
        self.assertEqual(aura.mode, "RGBA")
        self.assertGreater(aura.getchannel("A").getextrema()[1], 0)
        self.assertIs(pet._aura_layer(120, 130, "calm"), aura)

    def test_compose_leaves_corners_transparent(self) -> None:
        original = Image.new("RGBA", (100, 100), (200, 100, 50, 255))
        frame = pet._compose_frame(original, 100, 100, "happy")
        self.assertEqual(frame.size, (160, 160))
        self.assertEqual(frame.getpixel((0, 0))[3], 0)
        self.assertEqual(frame.getpixel((80, 80))[3], 255)

    def test_compose_changes_with_mood(self) -> None:
        original = Image.new("RGBA", (80, 80), (200, 200, 200, 255))
        calm = pet._compose_frame(original, 80, 80, "calm")
        happy = pet._compose_frame(original, 80, 80, "happy")
        self.assertNotEqual(calm.getpixel((40, 40)), happy.getpixel((40, 40)))

    def test_clamp_position_keeps_window_on_screen(self) -> None:
        self.assertEqual(pet._clamp_position(-40, -10, 200, 200, 300, 300), (0, 0))
        self.assertEqual(pet._clamp_position(500, 500, 200, 200, 300, 300), (100, 100))
        self.assertEqual(pet._clamp_position(60, 70, 200, 200, 300, 300), (60, 70))


if __name__ == "__main__":
    unittest.main()
