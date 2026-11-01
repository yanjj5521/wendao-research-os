"""Group red-packet share splitting.

A packet has a fixed total and a fixed number of shares.  Each claim takes at
most twice the current average, leaving at least one unit for every remaining
share; the last claim receives the remainder.  This keeps the sum exact and
avoids a zero-yield final share.
"""
from __future__ import annotations

import secrets


def split_share(
    remaining_amount: int, remaining_shares: int, randbelow=secrets.randbelow
) -> int:
    remaining_amount = int(remaining_amount)
    remaining_shares = int(remaining_shares)
    if remaining_shares <= 0:
        raise ValueError("红包份额已用完。")
    if remaining_amount < remaining_shares:
        raise ValueError("红包余额不足以继续分配。")
    if remaining_shares == 1:
        return remaining_amount
    reserve = remaining_amount - (remaining_shares - 1)
    fair = max(1, (remaining_amount // remaining_shares) * 2)
    upper = max(1, min(reserve, fair))
    return 1 + int(randbelow(upper))
