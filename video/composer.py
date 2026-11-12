"""Video Composer — Creates the final video from charts, narration, and music.

Uses ffmpeg for composition. Background music at -28dB.
"""
import json
import logging
import subprocess
import shutil
from pathlib import Path
from datetime import datetime

from config.settings import (
    VIDEOS_DIR, CHARTS_DIR, AUDIO_DIR, VIDEO_RESOLUTION, VIDEO_FPS,
    MUSIC_DIR, MUSIC_VOLUME_DB, BRAND_NAME,
)

logger = logging.getLogger("alpha_trading_floor")


def _extract_ticker_order(script_text: str, all_tickers: list) -> list:
    """Parse the script narration to find the order tickers are first mentioned.

    Returns tickers in the order they appear in the script, so chart slides
    match what the narrator is talking about. Falls back to empty list
    if no tickers are found.
    """
    if not script_text:
        return []

    seen = set()
    ordered = []
    # Search for each ticker in the script text, case-insensitive
    # Use word boundaries to avoid partial matches (e.g., "A" in "AMZN")
    for word in script_text.split():
        upper = word.strip(".,;:!?()[]{}\"'").upper()
        for ticker in all_tickers:
            if upper == ticker and ticker not in seen:
                seen.add(ticker)
                ordered.append(ticker)

    return ordered


def _compute_ticker_durations(script_text: str, all_tickers: list,
                               total_ticker_time: float) -> dict:
    """Compute how many seconds each ticker's chart should display.

    Splits the script into sections by ticker mentions and allocates
    time proportionally. Tickers mentioned more get longer chart time.
    """
    if not script_text or not all_tickers:
        return {}

    # Count how many words are in each ticker's "section"
    # A section starts when a ticker is mentioned and continues until the next ticker
    words = script_text.split()
    n_words = len(words)

    # Find positions where each ticker is mentioned
    ticker_positions = {t: [] for t in all_tickers}
    for i, word in enumerate(words):
        clean = word.strip(".,;:!?()[]{}\"'").upper()
        if clean in ticker_positions:
            ticker_positions[clean].append(i)

    # If no positions found, fall back to equal time
    total_mentions = sum(len(v) for v in ticker_positions.values())
    if total_mentions == 0:
        equal_time = total_ticker_time / max(len(all_tickers), 1)
        return {t: equal_time for t in all_tickers}

    # Allocate time proportional to mention count
    # Minimum 8 seconds per ticker so even briefly-mentioned ones are visible
    min_time = 8.0
    durations = {}
    remaining_time = total_ticker_time - (min_time * len(all_tickers))

    for ticker in all_tickers:
        mentions = len(ticker_positions.get(ticker, []))
        durations[ticker] = min_time

    if remaining_time > 0 and total_mentions > 0:
        for ticker in all_tickers:
            mentions = len(ticker_positions.get(ticker, []))
            proportional = (mentions / total_mentions) * remaining_time
            durations[ticker] = min_time + proportional

    # Order by narration sequence
    narration_order = _extract_ticker_order(script_text, all_tickers)
    ordered_durations = {}
    for ticker in narration_order:
        if ticker in durations:
            ordered_durations[ticker] = durations[ticker]
    # Add any tickers not found in narration at the end
    for ticker in all_tickers:
        if ticker not in ordered_durations:
            ordered_durations[ticker] = durations.get(ticker, min_time)

    return ordered_durations


def get_audio_duration(path: str) -> float:
    """Get audio duration in seconds."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 120.0


def _select_background_music() -> str:
    """Select a background music file for the video."""
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)

    music_files = [
        p for p in (
            list(MUSIC_DIR.glob("*.mp3")) +
            list(MUSIC_DIR.glob("*.wav")) +
            list(MUSIC_DIR.glob("*.m4a"))
        )
        if not p.name.startswith("._") and p.is_file() and p.stat().st_size > 1024
    ]

    if music_files:
        import random
        selected = random.choice(music_files)
        logger.info(f"Selected background music: {selected.name}")
        return str(selected)

    # Fallback: generate ambient tone
    logger.info("No music files found — generating ambient background tone")
    ambient_path = MUSIC_DIR / "ambient_finance.mp3"
    if not ambient_path.exists():
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=110:duration=180",
            "-f", "lavfi", "-i", "sine=frequency=165:duration=180",
            "-filter_complex",
            "[0:a]volume=-18dB,afade=t=in:st=0:d=3,afade=t=out:st=177:d=3[bass];"
            "[1:a]volume=-24dB,afade=t=in:st=0:d=3,afade=t=out:st=177:d=3[pad];"
            "[bass][pad]amix=inputs=2:duration=longest[out]",
            "-map", "[out]",
            "-c:a", "libmp3lame", "-b:a", "128k",
            str(ambient_path)
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        logger.info(f"Generated ambient tone: {ambient_path}")

    return str(ambient_path)


def compose_video(stock_data: dict, script_data: dict, audio_path: str,
                   alpha_scores: dict = None, chart_paths: dict = None) -> Path:
    """Compose the final video from charts, narration, and music.

    Creates: title card → Alpha Score overview → ticker charts (ranked by score) → outro
    """
    today = datetime.now().strftime("%Y-%m-%d")
    output_dir = VIDEOS_DIR / today
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "video.mp4"

    audio_duration = get_audio_duration(audio_path)
    logger.info(f"Audio duration: {audio_duration:.1f}s")

    # Rank tickers by script narration order (first mention in script)
    # Falls back to Alpha Score order if script parsing fails
    script_text = script_data.get("script", "")
    narration_order = _extract_ticker_order(script_text, list(stock_data.keys()))
    if narration_order:
        ranked_tickers = narration_order
        logger.info(f"Chart order from narration: {ranked_tickers}")
    elif alpha_scores:
        ranked_tickers = sorted(alpha_scores.keys(),
                                 key=lambda t: alpha_scores[t]["alpha_score"], reverse=True)
        logger.info(f"Chart order from Alpha Scores: {ranked_tickers}")
    else:
        ranked_tickers = list(stock_data.keys())
        logger.info(f"Chart order from stock_data keys: {ranked_tickers}")

    num_tickers = len(ranked_tickers)

    # Time allocation:
    # 5s title, 3s alpha overview, ticker charts (proportional to narration), 3s alpha pick, 3s watchlist, 5s outro
    title_duration = 5
    overview_duration = 3
    alpha_pick_duration = 3
    watchlist_duration = 3
    outro_duration = 5

    fixed_time = title_duration + overview_duration + alpha_pick_duration + watchlist_duration + outro_duration
    total_ticker_time = audio_duration - fixed_time

    # Compute proportional durations based on how much each ticker is discussed
    ticker_durations = _compute_ticker_durations(
        script_text, list(stock_data.keys()), total_ticker_time
    )
    if not ticker_durations:
        # Fallback: equal time
        equal_time = total_ticker_time / max(num_tickers, 1)
        ticker_durations = {t: equal_time for t in ranked_tickers}

    logger.info(f"Ticker durations: { {t: f'{d:.1f}s' for t, d in ticker_durations.items()} }")

    if chart_paths is None:
        chart_paths = {}

    segments = []

    # ── Title card ────────────────────────────────────────────────────────
    title_card = chart_paths.get("title_card", "")
    if title_card and Path(title_card).exists():
        seg_path = output_dir / "seg_title.mp4"
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", title_card,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
            "-t", str(title_duration),
            "-pix_fmt", "yuv420p",
            "-vf", f"scale={VIDEO_RESOLUTION[0]}:{VIDEO_RESOLUTION[1]}:force_original_aspect_ratio=decrease,pad={VIDEO_RESOLUTION[0]}:{VIDEO_RESOLUTION[1]}:-1:-1:color=black",
            "-r", str(VIDEO_FPS),
            str(seg_path)
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=60)
        segments.append(str(seg_path))

    # ── Alpha Score overview card ─────────────────────────────────────────
    overview_card = chart_paths.get("title_card", "")  # Reuse title card as overview
    if overview_card and Path(overview_card).exists():
        seg_path = output_dir / "seg_overview.mp4"
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", overview_card,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
            "-t", str(overview_duration),
            "-pix_fmt", "yuv420p",
            "-vf", f"scale={VIDEO_RESOLUTION[0]}:{VIDEO_RESOLUTION[1]}:force_original_aspect_ratio=decrease,pad={VIDEO_RESOLUTION[0]}:{VIDEO_RESOLUTION[1]}:-1:-1:color=black",
            "-r", str(VIDEO_FPS),
            str(seg_path)
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=60)
        segments.append(str(seg_path))

    # ── Ticker charts (in narration order, proportional duration) ──────────
    for ticker in ranked_tickers:
        chart_key = f"{ticker}_chart"
        chart_path = chart_paths.get(chart_key, "")
        ticker_dur = ticker_durations.get(ticker, total_ticker_time / max(num_tickers, 1))
        if chart_path and Path(chart_path).exists():
            seg_path = output_dir / f"seg_{ticker}.mp4"
            cmd = [
                "ffmpeg", "-y", "-loop", "1", "-i", chart_path,
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
                "-t", str(ticker_dur),
                "-pix_fmt", "yuv420p",
                "-vf", f"scale={VIDEO_RESOLUTION[0]}:{VIDEO_RESOLUTION[1]}:force_original_aspect_ratio=decrease,pad={VIDEO_RESOLUTION[0]}:{VIDEO_RESOLUTION[1]}:-1:-1:color=black",
                "-r", str(VIDEO_FPS),
                str(seg_path)
            ]
            subprocess.run(cmd, capture_output=True, check=True, timeout=max(180, int(ticker_dur) + 60))
            segments.append(str(seg_path))

    # ── Alpha Pick card (reuse top ticker chart) ───────────────────────────
    if ranked_tickers:
        top_ticker = ranked_tickers[0]
        chart_key = f"{top_ticker}_chart"
        chart_path = chart_paths.get(chart_key, "")
        if chart_path and Path(chart_path).exists():
            seg_path = output_dir / "seg_alpha_pick.mp4"
            cmd = [
                "ffmpeg", "-y", "-loop", "1", "-i", chart_path,
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
                "-t", str(alpha_pick_duration),
                "-pix_fmt", "yuv420p",
                "-vf", f"scale={VIDEO_RESOLUTION[0]}:{VIDEO_RESOLUTION[1]}:force_original_aspect_ratio=decrease,pad={VIDEO_RESOLUTION[0]}:{VIDEO_RESOLUTION[1]}:-1:-1:color=black",
                "-r", str(VIDEO_FPS),
                str(seg_path)
            ]
            subprocess.run(cmd, capture_output=True, check=True, timeout=60)
            segments.append(str(seg_path))

    # ── Outro card ────────────────────────────────────────────────────────
    if title_card and Path(title_card).exists():
        seg_path = output_dir / "seg_outro.mp4"
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", title_card,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
            "-t", str(outro_duration),
            "-pix_fmt", "yuv420p",
            "-vf", f"scale={VIDEO_RESOLUTION[0]}:{VIDEO_RESOLUTION[1]}:force_original_aspect_ratio=decrease,pad={VIDEO_RESOLUTION[0]}:{VIDEO_RESOLUTION[1]}:-1:-1:color=black",
            "-r", str(VIDEO_FPS),
            str(seg_path)
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=60)
        segments.append(str(seg_path))

    if not segments:
        raise RuntimeError("No video segments generated")

    # ── Concatenate all segments ──────────────────────────────────────────
    concat_file = output_dir / "concat.txt"
    with open(concat_file, "w") as f:
        for seg in segments:
            f.write(f"file '{seg}'\n")

    slideshow_path = output_dir / "slideshow.mp4"
    voice_path = output_dir / "with_voice.mp4"

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        str(slideshow_path)
    ]
    subprocess.run(cmd, capture_output=True, check=True, timeout=300)

    # Add narration audio
    cmd = [
        "ffmpeg", "-y",
        "-i", str(slideshow_path),
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v", "-map", "1:a",
        "-shortest",
        str(voice_path)
    ]
    subprocess.run(cmd, capture_output=True, check=True, timeout=300)

    # ── Mix background music at -28dB ─────────────────────────────────────
    music_path = ""
    try:
        music_path = _select_background_music()
        if music_path and Path(music_path).exists():
            logger.info(f"Mixing background music: {music_path}")
            cmd = [
                "ffmpeg", "-y",
                "-i", str(voice_path),
                "-stream_loop", "-1",
                "-i", music_path,
                "-filter_complex",
                f"[1:a]volume={MUSIC_VOLUME_DB}dB,atrim=0:{audio_duration}[music];"
                f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=3[aout]",
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                str(output_path)
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=300)
            if result.returncode != 0:
                logger.warning(f"Music mixing failed, using voice-only")
                shutil.move(str(voice_path), str(output_path))
            else:
                voice_path.unlink(missing_ok=True)
                logger.info("Background music mixed successfully")
    except Exception as e:
        logger.warning(f"Music mixing error: {e}, using voice-only")
        shutil.move(str(voice_path), str(output_path))

    if not output_path.exists():
        shutil.move(str(voice_path), str(output_path))

    # Clean up temp files
    for seg in segments:
        Path(seg).unlink(missing_ok=True)
    slideshow_path.unlink(missing_ok=True)
    concat_file.unlink(missing_ok=True)

    logger.info(f"Video saved: {output_path} ({output_path.stat().st_size / 1024 / 1024:.1f}MB)")
    return output_path
