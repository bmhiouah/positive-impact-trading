"""A limit on the whole book, checked before a new buy is sent.

Each trade already risks a fixed slice of the account. This gate stops the
morning when several names signal together from stacking those slices past
a cap, and it stops the book from holding more names than it can watch.
"""

from __future__ import annotations

from dataclasses import dataclass

from marketmind import config


@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: str


def admit(equity: float, open_positions: int, open_risk: float,
          new_risk: float, *,
          max_positions: int = config.MAX_OPEN_POSITIONS,
          max_book_risk: float = config.MAX_BOOK_RISK) -> Admission:
    """Whether one more buy fits under the book limits.

    ``open_risk`` and ``new_risk`` are dollars that would be lost if every
    stop filled. ``equity`` is the account value in the same currency.
    """
    try:
        equity_f = float(equity)
        open_risk_f = float(open_risk)
        new_risk_f = float(new_risk)
        held = int(open_positions)
    except (TypeError, ValueError):
        return Admission(False, "The account value could not be read.")
    if held >= max_positions:
        return Admission(
            False,
            f"The book already holds {held} names. The limit is {max_positions}.",
        )
    if equity_f <= 0:
        return Admission(False, "The account value is not available yet.")
    if new_risk_f <= 0:
        return Admission(False, "This buy has no stop distance to size from.")
    room = max_book_risk * equity_f
    if open_risk_f + new_risk_f > room + 1e-6:
        return Admission(
            False,
            f"This buy would put more than {max_book_risk:.0%} of the account "
            "at risk across open stops.",
        )
    return Admission(True, "")
