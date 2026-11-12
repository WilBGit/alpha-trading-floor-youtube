# Alpha Trading Floor YouTube Content Pipeline

[![CI](https://github.com/WilBGit/alpha-trading-floor-youtube/actions/workflows/ci.yml/badge.svg)](https://github.com/WilBGit/alpha-trading-floor-youtube/actions/workflows/ci.yml)

The production-oriented content pipeline for the Alpha Trading Floor YouTube channel. It combines deterministic market-data collection, technical analysis, catalyst research, Alpha scoring, script and title generation, script QC, chart and thumbnail creation, narration, ffmpeg composition, and an explicit YouTube upload boundary.

This is separate from the smaller reusable `youtube-generator` repository. `youtube-generator` contains the general visual-card and video assembly utility. This repository contains the Alpha Trading Floor market-to-video pipeline.

## Pipeline

1. Collect market, earnings, news, StockTwits, and Finviz inputs.
2. Compute technical levels, market regime, catalysts, and Alpha Scores.
3. Generate a script and titles through the configured Hermes/Codex path.
4. Run deterministic quality checks for completeness, length, duplicated material, price claims, and required disclaimers.
5. Create charts, thumbnail assets, narration, and the final video.
6. Upload only when an operator explicitly enables it and the thumbnail passes validation.

The published copy defaults to no upload. `python run.py` runs the pipeline without a YouTube write. `python run.py --upload` is an explicit opt-in. Upload privacy defaults to `private` and can be changed only through the runtime variable `YOUTUBE_PRIVACY_STATUS`.

## What is excluded

Credentials, Google OAuth state, databases, downloaded market data, logs, generated audio, thumbnails, videos, charts, music, virtual environments, and runtime markers are excluded. Configure paths with environment variables such as `ALPHA_YOUTUBE_PROJECT_DIR`, `GOOGLE_TOKEN_PATH`, and `GOOGLE_ENV_PATH`; never put their values in Git.

## Local validation

Use the project virtual environment and install the dependencies required by the pipeline modules. Safe checks include:

    python -m compileall -q .
    python run.py --help
    python preflight_check.py --help
    bash -n alpha_trading_floor_daily.sh

Full market-data collection, LLM generation, narration, ffmpeg composition, OAuth refresh, and YouTube upload require the local runtime dependencies and are not run as part of repository publication validation.

## Content and platform policy

The channel is research and education content, not a promise of returns or an authorization to trade. Every release must retain its disclaimer, distinguish proxy data from exchange-authoritative data, and be reviewed before publication. Do not publish a video with an unverified price, missing thumbnail, incomplete script, stale generated asset, or unresolved runtime error.
