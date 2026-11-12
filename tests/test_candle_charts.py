import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from visuals.charts import generate_ticker_chart


SAMPLE_DATA = {
    "history_dates": [f"2026-01-{day:02d}" for day in range(2, 12)],
    "history_opens": [100, 101, 99, 102, 104, 103, 106, 105, 107, 108],
    "history_highs": [103, 104, 102, 105, 106, 105, 109, 108, 110, 111],
    "history_lows": [98, 99, 97, 100, 102, 101, 104, 103, 105, 106],
    "history_closes": [101, 99, 102, 104, 103, 106, 105, 107, 108, 110],
    "history_volumes": [1_000_000, 1_100_000, 900_000, 1_500_000, 1_300_000,
                        1_700_000, 1_200_000, 1_800_000, 1_600_000, 2_000_000],
    "current_price": 110.0,
    "change_pct": 1.85,
    "company_name": "Example Holdings",
    "volume": 2_000_000,
    "supports": [102.0],
    "resistances": [112.0],
}


class CandleChartTests(unittest.TestCase):
    def test_generates_light_candles_with_volume_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("visuals.charts.CHARTS_DIR", Path(tmp)):
                output = generate_ticker_chart("TEST", SAMPLE_DATA)

            self.assertTrue(output.exists())
            with Image.open(output) as image:
                self.assertGreaterEqual(image.width, 1800)
                self.assertGreaterEqual(image.height, 900)
                pixels = image.convert("RGB").getdata()
                light_pixels = sum(1 for pixel in pixels if min(pixel) > 235)
                green_pixels = sum(1 for r, g, b in pixels if g > 100 and g > r * 1.25 and g > b * 0.95)
                red_pixels = sum(1 for r, g, b in pixels if r > 150 and r > g * 1.35 and r > b * 1.2)

            self.assertGreater(light_pixels, 100_000)
            self.assertGreater(green_pixels, 100)
            self.assertGreater(red_pixels, 100)


if __name__ == "__main__":
    unittest.main()
