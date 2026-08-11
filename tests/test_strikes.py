"""Unit tests for option strike scaffolding."""
from __future__ import annotations

import unittest
from datetime import date

from nifty_scanner.screener import strikes


class StrikeTests(unittest.TestCase):
    def test_equity_step_bands(self):
        self.assertEqual(strikes.strike_step(40), 2.5)
        self.assertEqual(strikes.strike_step(180), 5.0)
        self.assertEqual(strikes.strike_step(720), 10.0)
        self.assertEqual(strikes.strike_step(1800), 25.0)
        self.assertEqual(strikes.strike_step(3200), 50.0)

    def test_round_strike(self):
        self.assertEqual(strikes.round_strike(1012, 10), 1010)
        self.assertEqual(strikes.round_strike(2488, 25), 2500)

    def test_bull_spread_order(self):
        idea = strikes.suggest_for_underlying(
            "RELIANCE", 2850, "Bullish", atr=40, as_of=date(2026, 8, 3)
        )
        self.assertEqual(idea["buy_option"], "CE")
        self.assertGreater(idea["sell_strike"], idea["buy_strike"])
        self.assertEqual(idea["bias"], "Bullish")

    def test_bear_spread_order(self):
        idea = strikes.suggest_for_underlying(
            "INFY", 1450, "Bearish", atr=25, as_of=date(2026, 8, 3)
        )
        self.assertEqual(idea["buy_option"], "PE")
        self.assertLess(idea["sell_strike"], idea["buy_strike"])
        self.assertEqual(idea["bias"], "Bearish")

    def test_monthly_expiry_is_thursday(self):
        exp = strikes.next_monthly_expiry(date(2026, 8, 3))
        self.assertEqual(exp.weekday(), 3)
        self.assertGreaterEqual(exp, date(2026, 8, 3))


if __name__ == "__main__":
    unittest.main()
