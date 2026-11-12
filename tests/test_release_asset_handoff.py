import hashlib
import importlib.util
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image, ImageDraw

from youtube.upload import generate_and_upload_thumbnail

from visuals.cover_gen import (
    facebook_cover_pixel_sha256,
    generate_dynamic_thumbnail,
    load_facebook_cover_for_date,
    replicate_facebook_cover,
)
from youtube.upload import generate_description


FB_MIRROR_PATH = Path.home() / ".hermes" / "scripts" / "alpha_x_to_facebook.py"


def load_fb_mirror_module():
    spec = importlib.util.spec_from_file_location("alpha_x_to_facebook_test", FB_MIRROR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReleaseAssetHandoffTests(unittest.TestCase):
    def test_facebook_asset_is_copied_to_manifest_without_secrets(self):
        module = load_fb_mirror_module()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "facebook.png"
            image = Image.new("RGB", (1280, 720), "#274c77")
            ImageDraw.Draw(image).rectangle((800, 120, 1200, 600), fill="#d97732")
            image.save(source)
            manifest = root / "manifest.json"
            asset_root = root / "covers"

            with patch.dict(
                os.environ,
                {
                    "ALPHA_FACEBOOK_YOUTUBE_COVER_MANIFEST": str(manifest),
                    "ALPHA_FACEBOOK_YOUTUBE_COVER_DIR": str(asset_root),
                },
                clear=False,
            ):
                record = module.persist_facebook_youtube_cover(
                    str(source),
                    {"x_post_id": "x-123", "text": "AMD has a catalyst."},
                    {"post_id": "fb-456", "permalink_url": "https://facebook.example/post"},
                )

            copied = Path(record["youtube_cover_path"])
            self.assertTrue(copied.exists())
            self.assertEqual(
                hashlib.sha256(copied.read_bytes()).hexdigest(),
                record["image_sha256"],
            )
            payload = json.loads(manifest.read_text())
            self.assertEqual(payload["status"], "verified")
            self.assertEqual(payload["x_post_id"], "x-123")
            self.assertNotIn("access_token", json.dumps(payload).lower())

    def test_same_day_facebook_cover_is_loaded_and_stale_cover_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "facebook.png"
            image = Image.new("RGB", (1280, 720), "#274c77")
            ImageDraw.Draw(image).rectangle((800, 120, 1200, 600), fill="#d97732")
            image.save(source)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "verified",
                        "release_date": "2026-11-06",
                        "youtube_cover_path": str(source),
                        "image_sha256": digest,
                    }
                )
            )
            with patch.dict(
                os.environ,
                {"ALPHA_FACEBOOK_YOUTUBE_COVER_MANIFEST": str(manifest)},
                clear=False,
            ):
                self.assertEqual(
                    load_facebook_cover_for_date("2026-11-06"), source,
                )
                self.assertIsNone(load_facebook_cover_for_date("2026-11-07"))

            source.write_bytes(b"tampered")
            with patch.dict(
                os.environ,
                {"ALPHA_FACEBOOK_YOUTUBE_COVER_MANIFEST": str(manifest)},
                clear=False,
            ):
                self.assertIsNone(load_facebook_cover_for_date("2026-11-06"))

    def test_invalid_verified_cover_does_not_fall_back_to_title_card(self):
        with TemporaryDirectory() as tmp:
            current = Path(tmp) / "missing.png"
            with self.assertRaises(FileNotFoundError):
                generate_and_upload_thumbnail(
                    {
                        "thumbnail": str(current),
                        "thumbnail_source": "facebook_verified_same_day",
                        "title_card": str(Path(tmp) / "title-card.png"),
                    },
                    {},
                    {},
                )

    def test_replicated_cover_is_exactly_youtube_sized(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "facebook.jpg"
            image = Image.new("RGB", (1600, 900), "#1c3144")
            ImageDraw.Draw(image).rectangle((900, 150, 1500, 750), fill="#d97732")
            image.save(source, quality=92)
            output = root / "youtube.png"

            result = replicate_facebook_cover(source, output)

            self.assertEqual(result, output)
            with Image.open(output) as rendered:
                self.assertEqual(rendered.size, (1280, 720))
                self.assertEqual(rendered.mode, "RGB")

    def test_production_thumbnail_entrypoint_uses_facebook_cover(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "facebook.png"
            image = Image.new("RGB", (1280, 720), "#1c3144")
            ImageDraw.Draw(image).rectangle((900, 150, 1500, 650), fill="#d97732")
            image.save(source)
            output = root / "youtube.png"

            result = generate_dynamic_thumbnail(
                {"AMD": {"current_price": 100.0}},
                {"AMD": {"alpha_score": 71.8, "classification": "CONSTRUCTIVE"}},
                {"title": "AMD at the Decision Point", "mode": "levels"},
                output_path=output,
                facebook_cover_path=source,
            )

            self.assertEqual(result, output)
            self.assertEqual(
                facebook_cover_pixel_sha256(source),
                facebook_cover_pixel_sha256(output),
            )
            with Image.open(output) as rendered:
                self.assertEqual(rendered.size, (1280, 720))

    def test_youtube_description_contains_key_levels_and_a_stronger_hook(self):
        description = generate_description(
            {"title": "AMD at the Decision Point"},
            {
                "AMD": {
                    "current_price": 100.0,
                    "change_pct": 1.2,
                    "supports": [98.0],
                    "resistances": [103.0],
                }
            },
            {"AMD": {"alpha_score": 71.8, "classification": "CONSTRUCTIVE"}},
            {"regime": "RISK_ON"},
        )

        self.assertIn("Support: $98.00", description)
        self.assertIn("Resistance: $103.00", description)
        self.assertIn("Level to watch: $98.00", description)
        self.assertIn("decision point", description.lower())


if __name__ == "__main__":
    unittest.main()
