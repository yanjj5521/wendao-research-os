from __future__ import annotations

import math
import struct
import unittest

from services.integral import (
    IntegralError,
    integrate,
    parse_binary,
    parse_text,
    render_svg,
)


class TextParsingTests(unittest.TestCase):
    def test_two_columns_whitespace_with_header_and_comments(self) -> None:
        raw = b"# note\nx y\n0 0\n0.5 0.25\n1 1\n"
        data = parse_text(raw)
        self.assertEqual(data.x, [0.0, 0.5, 1.0])
        self.assertEqual(data.y, [0.0, 0.25, 1.0])
        self.assertTrue(any("非数值" in note for note in data.notes))

    def test_comma_separated_values(self) -> None:
        data = parse_text(b"x,y\n0,1\n2,3\n")
        self.assertEqual(data.x, [0.0, 2.0])
        self.assertEqual(data.y, [1.0, 3.0])

    def test_single_column_uses_row_index(self) -> None:
        data = parse_text(b"2\n4\n6\n")
        self.assertEqual(data.x, [0.0, 1.0, 2.0])
        self.assertEqual(data.y, [2.0, 4.0, 6.0])
        self.assertTrue(any("行号" in note for note in data.notes))

    def test_unsorted_and_duplicate_x_are_repaired(self) -> None:
        data = parse_text(b"2 4\n0 0\n1 1\n1 9\n")
        self.assertEqual(data.x, [0.0, 1.0, 2.0])
        self.assertEqual(data.y, [0.0, 9.0, 4.0])
        self.assertTrue(any("排序" in note for note in data.notes))
        self.assertTrue(any("重复" in note for note in data.notes))

    def test_garbage_input_is_rejected(self) -> None:
        with self.assertRaises(IntegralError):
            parse_text(b"no numbers here\n")


class BinaryParsingTests(unittest.TestCase):
    def test_interleaved_float64_little_endian(self) -> None:
        raw = struct.pack("<4d", 0.0, 0.0, 1.0, 1.0)
        data = parse_binary(raw, dtype="float64", endian="little", layout="xy")
        self.assertEqual(data.x, [0.0, 1.0])
        self.assertEqual(data.y, [0.0, 1.0])

    def test_single_column_float32_big_endian(self) -> None:
        raw = struct.pack(">3f", 1.0, 2.0, 3.0)
        data = parse_binary(raw, dtype="float32", endian="big", layout="y")
        self.assertEqual(data.y, [1.0, 2.0, 3.0])
        self.assertEqual(data.x, [0.0, 1.0, 2.0])

    def test_truncated_binary_is_rejected(self) -> None:
        with self.assertRaises(IntegralError):
            parse_binary(b"\x00" * 7, dtype="float64", layout="xy")

    def test_odd_pair_count_is_rejected(self) -> None:
        raw = struct.pack("<3d", 0.0, 1.0, 2.0)
        with self.assertRaises(IntegralError):
            parse_binary(raw, dtype="float64", layout="xy")


class IntegrationTests(unittest.TestCase):
    def test_trapezoid_line_is_exact(self) -> None:
        data = parse_text(b"0 0\n1 1\n2 2\n")
        result = integrate(data, "trapezoid")
        self.assertAlmostEqual(result.area, 2.0, places=12)

    def test_simpson_quadratic_is_exact(self) -> None:
        data = parse_text(b"0 0\n0.5 0.25\n1 1\n")
        result = integrate(data, "simpson")
        self.assertAlmostEqual(result.area, 1.0 / 3.0, places=12)
        self.assertAlmostEqual(result.simpson_area, 1.0 / 3.0, places=12)

    def test_auto_prefers_simpson_when_uniform(self) -> None:
        data = parse_text(b"0 0\n0.5 0.25\n1 1\n")
        result = integrate(data, "auto")
        self.assertEqual(result.method, "simpson")

    def test_non_uniform_falls_back_to_trapezoid(self) -> None:
        data = parse_text(b"0 0\n1 1\n3 9\n")
        result = integrate(data, "auto")
        self.assertEqual(result.method, "trapezoid")
        self.assertIsNone(result.simpson_area)

    def test_odd_interval_count_rejects_simpson(self) -> None:
        data = parse_text(b"0 0\n1 1\n2 4\n3 9\n")
        with self.assertRaises(IntegralError):
            integrate(data, "simpson")

    def test_signed_and_absolute_area(self) -> None:
        data = parse_text(b"0 1\n1 -1\n")
        result = integrate(data, "trapezoid")
        self.assertAlmostEqual(result.positive, 0.25, places=12)
        self.assertAlmostEqual(result.negative, -0.25, places=12)
        self.assertAlmostEqual(result.area, 0.0, places=12)
        self.assertAlmostEqual(result.absolute, 0.5, places=12)

    def test_moments_and_rms(self) -> None:
        data = parse_text(b"0 0\n1 1\n2 2\n")
        result = integrate(data, "simpson")
        self.assertAlmostEqual(result.mean_y, 1.0, places=12)
        self.assertAlmostEqual(result.centroid_x, 4.0 / 3.0, places=12)
        self.assertAlmostEqual(result.rms, math.sqrt(4.0 / 3.0), places=12)

    def test_formula_and_derivation_are_present(self) -> None:
        data = parse_text(b"0 0\n0.5 0.25\n1 1\n")
        result = integrate(data)
        self.assertIn("Simpson", result.formula)
        self.assertGreaterEqual(len(result.derivation), 5)


class SvgTests(unittest.TestCase):
    def test_svg_contains_curve_and_no_script(self) -> None:
        data = parse_text(b"0 0\n0.5 0.25\n1 1\n")
        svg = render_svg(data)
        self.assertIn("<svg", svg)
        self.assertIn("<polyline", svg)
        self.assertNotIn("<script", svg.lower())


if __name__ == "__main__":
    unittest.main()
