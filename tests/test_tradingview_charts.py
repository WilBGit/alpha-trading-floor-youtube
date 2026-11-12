import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from visuals.tradingview import build_tradingview_html, chart_image_is_usable


SAMPLE_DATA = {
    "history_dates": ["2026-07-27", "2026-07-28", "2026-07-29"],
    "history_opens": [100.0, 101.0, 102.0],
    "history_highs": [103.0, 104.0, 105.0],
    "history_lows": [99.0, 100.0, 101.0],
    "history_closes": [101.0, 102.0, 104.0],
    "history_volumes": [1_000_000, 1_200_000, 1_500_000],
    "current_price": 104.0,
    "change_pct": 1.96,
    "supports": [101.0],
    "resistances": [105.0],
}


class TradingViewChartTests(unittest.TestCase):
    def test_html_uses_verified_candles_and_beginner_labels(self):
        html = build_tradingview_html(
            "TEST",
            SAMPLE_DATA,
            {"alpha_score": 72, "classification": "CONSTRUCTIVE"},
            "file:///tmp/lightweight-charts.js",
        )
        self.assertIn("LightweightCharts", html)
        self.assertIn('"time": "2026-07-29"', html)
        self.assertIn('"close": 104.0', html)
        self.assertIn("Support / price floor", html)
        self.assertIn("Resistance / price ceiling", html)
        self.assertIn("Alpha Score 72", html)
        self.assertIn("Promising setup", html)
        self.assertIn("TradingView Lightweight Charts", html)

    def test_html_uses_light_broker_style_with_quote_header(self):
        html = build_tradingview_html(
            "TEST",
            SAMPLE_DATA,
            {"alpha_score": 72, "classification": "CONSTRUCTIVE"},
            "file:///tmp/lightweight-charts.js",
        )
        self.assertIn("background: #ffffff", html)
        self.assertIn("#131722", html)
        self.assertIn("O 102.00", html)
        self.assertIn("H 105.00", html)
        self.assertIn("L 101.00", html)
        self.assertIn("C 104.00", html)
        self.assertIn("Volume", html)
        self.assertIn("#089981", html)
        self.assertIn("#f23645", html)

    def test_blank_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blank.png"
            Image.new("RGB", (1920, 1080), "#0b0e14").save(path)
            self.assertFalse(chart_image_is_usable(path))

    def test_realistic_image_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chart.png"
            image = Image.new("RGB", (1920, 1080), "#0b0e14")
            draw = ImageDraw.Draw(image)
            for x in range(100, 1800, 30):
                color = "#26a69a" if (x // 30) % 2 else "#ef5350"
                draw.rectangle((x, 200, x + 12, 850), fill=color)
            image.save(path)
            self.assertTrue(chart_image_is_usable(path))


if __name__ == "__main__":
    unittest.main()
