import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageStat

from visuals.charts import generate_thumbnail
from visuals.cover_gen import (
    build_image_prompt,
    build_thumbnail_output_path,
    composite_cover,
    generate_background,
    generate_dynamic_thumbnail,
    generate_textless_fallback_cover,
    validate_background_asset,
)
from youtube.upload import generate_and_upload_thumbnail, validate_thumbnail
from unittest.mock import patch


class YoutubeThumbnailTests(unittest.TestCase):
    def _sample_inputs(self):
        return (
            {
                "NVDA": {
                    "current_price": 213.05,
                    "change_pct": 2.19,
                    "history_closes": [198, 201, 199, 204, 207, 211, 213],
                },
                "META": {
                    "current_price": 501.25,
                    "change_pct": -1.1,
                    "history_closes": [510, 507, 508, 504, 503, 502, 501],
                },
            },
            {
                "NVDA": {"alpha_score": 52, "classification": "WEAK"},
                "META": {"alpha_score": 49.1, "classification": "HIGH RISK"},
            },
            {"title": "NVDA Leads With 52 Before Earnings | August 25, 2026"},
        )

    def test_generate_thumbnail_honors_per_video_output_path(self):
        stock_data, alpha_scores, script_data = self._sample_inputs()
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "video" / "thumbnail.png"
            result = generate_thumbnail(
                stock_data,
                alpha_scores,
                script_data,
                output_path=output,
            )

            self.assertEqual(Path(result), output)
            self.assertTrue(output.exists())
            with Image.open(output) as image:
                self.assertEqual(image.size, (1280, 720))
                self.assertGreater(max(ImageStat.Stat(image.convert("RGB")).stddev), 12)

    def test_thumbnail_selector_prefers_current_run_asset(self):
        with TemporaryDirectory() as tmp:
            current = Path(tmp) / "current_thumbnail.png"
            Image.new("RGB", (1280, 720), "#123456").save(current)

            selected = generate_and_upload_thumbnail(
                {"thumbnail": str(current)},
                {},
                {},
            )

            self.assertEqual(Path(selected), current)

    def test_image_prompt_matches_alpha_trade_desk_style(self):
        prompt = build_image_prompt("default", "AMD", 71.8, "AMD Before Earnings")
        self.assertIn("warm amber", prompt.lower())
        self.assertIn("cool blue", prompt.lower())
        self.assertIn("no readable words", prompt.lower())

    def test_background_provider_chain_tries_grok_after_gpt_failure(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "thumbnail_amd.png"
            grok_background = Path(tmp) / "thumbnail_amd_grok_bg.png"
            with patch("visuals.cover_gen.generate_background_with_hermes", return_value=None) as gpt:
                with patch("visuals.cover_gen.generate_background_with_xai", return_value=grok_background) as grok:
                    result = generate_background("prompt", output)

            self.assertEqual(result, grok_background)
            gpt.assert_called_once_with("prompt", output, timeout=120)
            grok.assert_called_once_with("prompt", output, timeout=120)

    def test_thumbnail_output_path_is_episode_specific(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = build_thumbnail_output_path(
                {"title": "AMD Scores 71.8 Today"}, "AMD", 71.8, output_dir=root
            )
            second = build_thumbnail_output_path(
                {"title": "NVDA Leads Before Earnings"}, "NVDA", 63.4, output_dir=root
            )

            self.assertNotEqual(first, second)
            self.assertEqual(first.parent, root)
            self.assertTrue(first.name.startswith("thumbnail_amd_"))
            self.assertTrue(second.name.startswith("thumbnail_nvda_"))

    def test_background_asset_validation_rejects_blank_and_accepts_real_image(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = root / "valid.png"
            blank = root / "blank.png"
            Image.new("RGB", (1280, 720), "#101820").save(valid)
            image = Image.new("RGB", (1280, 720), "#101820")
            from PIL import ImageDraw
            ImageDraw.Draw(image).rectangle((700, 120, 1100, 600), fill="#3fb950")
            image.save(blank)

            self.assertTrue(validate_background_asset(blank))
            self.assertFalse(validate_background_asset(valid))

    def test_image_prompt_requests_a_strong_visual_anchor(self):
        prompt = build_image_prompt("single_ticker", "AMD", 71.8, "AMD Before Earnings")
        self.assertIn("prominent monitor or sheet of paper", prompt.lower())

    def test_textless_mode_uses_photo_enhancement(self):
        from visuals.cover_gen import enhance_textless_background

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            background = root / "background.png"
            output = root / "cover.png"
            Image.new("RGB", (1280, 720), "#34546a").save(background)

            with patch(
                "visuals.cover_gen.enhance_textless_background",
                wraps=enhance_textless_background,
            ) as enhancer:
                composite_cover(
                    background_path=background,
                    top_ticker="AMD",
                    top_score=71.8,
                    top_class="CONSTRUCTIVE",
                    change_pct=1.8,
                    price=466.1,
                    hook="BEFORE EARNINGS",
                    mode="levels",
                    output_path=output,
                    stock_data={"AMD": {"history_closes": [450, 460, 466]}},
                    alpha_scores={"AMD": {"alpha_score": 71.8}},
                    include_market_overlay=False,
                )

            enhancer.assert_called_once()
            with Image.open(output) as rendered:
                self.assertEqual(rendered.size, (1280, 720))

    def test_textless_fallback_cover_is_unique_and_valid(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "thumbnail_textless_fallback.png"
            result = generate_textless_fallback_cover(
                {"AMD": {"current_price": 466.1, "change_pct": 1.8, "history_closes": [450, 453, 458, 461, 466]}},
                {"AMD": {"alpha_score": 71.8, "classification": "CONSTRUCTIVE"}},
                {"title": "AMD Before Earnings", "mode": "levels"},
                output_path=output,
            )
            self.assertEqual(Path(result), output)
            self.assertTrue(validate_thumbnail(output))
            with Image.open(output) as rendered:
                self.assertEqual(rendered.size, (1280, 720))
                self.assertGreater(max(ImageStat.Stat(rendered).stddev), 12)

    def test_dynamic_thumbnail_provider_failure_uses_textless_fallback(self):
        stock_data = {
            "AMD": {
                "current_price": 466.1,
                "change_pct": 1.8,
                "history_closes": [450, 453, 458, 461, 466],
            }
        }
        alpha_scores = {"AMD": {"alpha_score": 71.8, "classification": "CONSTRUCTIVE"}}
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "dynamic.png"
            with patch("visuals.cover_gen.generate_background", return_value=None):
                result = generate_dynamic_thumbnail(
                    stock_data,
                    alpha_scores=alpha_scores,
                    script_data={"title": "AMD Before Earnings", "mode": "levels"},
                    output_path=output,
                )
            self.assertEqual(Path(result), output)
            self.assertTrue(validate_thumbnail(output))

    def test_score_is_embedded_in_monitor_region(self):
        from PIL import ImageDraw
        from visuals.cover_gen import composite_cover

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            background = root / "background.png"
            output = root / "cover.png"
            image = Image.new("RGB", (1280, 720), "#8a6f54")
            ImageDraw.Draw(image).rectangle((760, 80, 1240, 400), fill="#203a52")
            image.save(background)

            composite_cover(
                background_path=background,
                top_ticker="AMD",
                top_score=71.8,
                top_class="CONSTRUCTIVE",
                change_pct=1.8,
                price=466.1,
                hook="BEFORE EARNINGS",
                mode="levels",
                output_path=output,
                stock_data={"AMD": {"history_closes": [450, 460, 466]}},
                alpha_scores={"AMD": {"alpha_score": 71.8}},
                include_market_overlay=True,
            )

            with Image.open(output) as rendered:
                pixel = rendered.convert("RGB").getpixel((980, 190))
                self.assertGreater(pixel[1], 45)

    def test_score_badge_blends_with_background(self):
        from PIL import ImageDraw
        from visuals.cover_gen import composite_cover

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            background = root / "background.png"
            output = root / "cover.png"
            image = Image.new("RGB", (1280, 720), "#8a6f54")
            ImageDraw.Draw(image).rectangle((700, 100, 1200, 650), fill="#203a52")
            image.save(background)

            composite_cover(
                background_path=background,
                top_ticker="AMD",
                top_score=71.8,
                top_class="CONSTRUCTIVE",
                change_pct=1.8,
                price=466.1,
                hook="BEFORE EARNINGS",
                mode="levels",
                output_path=output,
                stock_data={"AMD": {"history_closes": [450, 460, 466]}},
                alpha_scores={"AMD": {"alpha_score": 71.8}},
                include_market_overlay=True,
            )

            with Image.open(output) as rendered:
                self.assertNotEqual(rendered.convert("RGB").getpixel((380, 500)), (22, 27, 34))

    def test_invalid_thumbnail_is_rejected(self):
        with TemporaryDirectory() as tmp:
            invalid = Path(tmp) / "bad.png"
            Image.new("RGB", (640, 360), "#123456").save(invalid)
            self.assertFalse(validate_thumbnail(invalid))


if __name__ == "__main__":
    unittest.main()
