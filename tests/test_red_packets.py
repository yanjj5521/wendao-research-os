from __future__ import annotations

import unittest

from hub.red_packets import split_share


class SplitShareTests(unittest.TestCase):
    def test_minimum_and_maximum_bounds(self) -> None:
        self.assertEqual(split_share(10, 3, lambda n: 0), 1)
        self.assertEqual(split_share(10, 3, lambda n: n - 1), 6)
        self.assertEqual(split_share(3, 3, lambda n: n - 1), 1)

    def test_last_share_gets_the_remainder(self) -> None:
        self.assertEqual(split_share(7, 1, lambda n: 0), 7)

    def test_distribution_sums_exactly_for_any_pick(self) -> None:
        for total, shares in ((3, 3), (10, 3), (100, 7), (20, 20)):
            for pick in (lambda n: 0, lambda n: n - 1, lambda n: n // 2):
                remaining = total
                left = shares
                amounts = []
                while left:
                    amount = split_share(remaining, left, pick)
                    amounts.append(amount)
                    remaining -= amount
                    left -= 1
                self.assertEqual(sum(amounts), total, (total, shares))
                self.assertTrue(all(amount >= 1 for amount in amounts))
                self.assertEqual(len(amounts), shares)

    def test_invalid_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            split_share(5, 0)
        with self.assertRaises(ValueError):
            split_share(2, 3)


if __name__ == "__main__":
    unittest.main()
