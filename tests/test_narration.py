import subprocess
import tempfile
import unittest
from pathlib import Path

from voice.narration import normalize_pronunciation, chunk_text, _join_and_master


class NarrationTests(unittest.TestCase):
    def test_ticker_replacements_do_not_cascade_or_replace_common_words(self):
        text = "VIX is at $18.21. ON is down 8.15%. Keep an eye on support."
        spoken = normalize_pronunciation(text, {
            "VIX": "V I X",
            "V": "Visa",
            "ON": "ON Semiconductor",
        })
        self.assertNotIn("Visa I X", spoken)
        self.assertNotIn("eye ON Semiconductor support", spoken)
        self.assertIn("V I X", spoken)
        self.assertIn("ON Semiconductor", spoken)

    def test_financial_numbers_are_explicitly_spoken(self):
        text = "SPY closed at $740.86, up 0.24%. Alpha Score 51.9."
        spoken = normalize_pronunciation(text, {"SPY": "S P Y"})
        self.assertIn("seven hundred forty dollars and eighty-six cents", spoken)
        self.assertIn("zero point two four percent", spoken)
        self.assertIn("fifty-one point nine", spoken)
        self.assertNotIn("$", spoken)
        self.assertNotIn("%", spoken)

    def test_visual_cues_and_section_timestamps_are_not_narrated(self):
        text = "1. HOOK — 0:00-0:20 You might disagree. [SHOW RANKING] 2. MARKET PULSE — 0:20-0:50 Buyers returned. 8. WHAT’S COMING NEXT — 10:15-10:40 Earnings arrive."
        spoken = normalize_pronunciation(text, {})
        self.assertNotIn("HOOK", spoken)
        self.assertNotIn("0:00", spoken)
        self.assertNotIn("SHOW RANKING", spoken)
        self.assertEqual(spoken, "You might disagree. Buyers returned. Earnings arrive.")

    def test_long_text_is_chunked_at_sentence_boundaries(self):
        text = " ".join(
            f"Sentence {i} has enough words to sound natural." for i in range(120)
        )
        chunks = chunk_text(text, max_chars=500)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))
        self.assertTrue(all(chunk.endswith(".") for chunk in chunks))
        self.assertEqual(" ".join(chunks), text)
    def test_mastering_works_with_standard_ffmpeg_build(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            source = work / "source.aiff"
            output = work / "master.wav"
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=0.2,volume=20dB", "-c:a", "pcm_s16be",
                str(source),
            ], capture_output=True, check=True)
            _join_and_master([source], output, work)
            self.assertTrue(output.exists())
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries", "stream=sample_rate",
                "-of", "default=nw=1:nk=1", str(output),
            ], capture_output=True, text=True, check=True)
            self.assertEqual(probe.stdout.strip(), "48000")
            levels = subprocess.run([
                "ffmpeg", "-hide_banner", "-i", str(output), "-af", "volumedetect",
                "-f", "null", "-",
            ], capture_output=True, text=True, check=True)
            peak_line = next(
                line for line in levels.stderr.splitlines() if "max_volume:" in line
            )
            peak_db = float(peak_line.split("max_volume:", 1)[1].split("dB", 1)[0])
            self.assertLessEqual(peak_db, -2.5)


if __name__ == "__main__":
    unittest.main()
