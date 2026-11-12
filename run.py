#!/usr/bin/env python3
"""Alpha Trading Floor — Pipeline Entry Point.

Usage:
    python run.py                  # Full pipeline without upload
    python run.py --upload         # Explicitly enable YouTube upload
    python run.py --tickers NVDA TSLA AAPL  # Custom ticker list
    python run.py --mode advice    # Force weekend/advice mode
    python run.py --mode levels    # Force weekday/levels mode

LOW TOKEN BURN: Only 2 LLM calls per run (script + title).
Everything else is deterministic Python.
"""
import sys
import argparse
from pathlib import Path

# Remove hermes-agent venv paths that conflict with project venv
sys.path = [p for p in sys.path if 'hermes-agent' not in p]

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "app"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

from app.main import run_pipeline

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=f"Alpha Trading Floor Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py                      # Full pipeline, no upload
  python run.py --upload               # Explicitly enable YouTube upload
  python run.py --tickers NVDA TSLA   # Custom tickers
  python run.py --mode advice          # Weekend finance advice content
  python run.py --mode levels          # Weekday stock analysis

Brand: Alpha Trading Floor
Tagline: Trade Smart. Manage Risk. Compound Wealth.
        """
    )
    parser.add_argument("--tickers", nargs="+", default=None,
                        help="Ticker symbols (default: dynamic from StockTwits)")
    parser.add_argument("--upload", action="store_true",
                        help="Explicitly enable YouTube upload")
    parser.add_argument("--mode", choices=["levels", "advice"], default=None,
                        help="Content mode: 'levels' (weekday) or 'advice' (weekend)")
    parser.add_argument("--init-db", action="store_true",
                        help="Initialize database tables and exit")

    args = parser.parse_args()

    # Database-only mode
    if args.init_db:
        print("Initializing database tables...")
        try:
            from database.db import init_database
            init_database()
            print("Database initialized successfully.")
        except Exception as e:
            print(f"Database initialization failed: {e}")
            print("Data will be saved to JSON files instead.")
        sys.exit(0)

    # Run the pipeline
    result = run_pipeline(
        tickers=args.tickers,
        upload=args.upload,
        mode=args.mode,
    )

    if result.get("errors"):
        print(f"\nCompleted with {len(result['errors'])} errors:")
        for e in result["errors"]:
            print(f"  - {e}")
        sys.exit(1)
    else:
        youtube_url = result.get("youtube", {}).get("url", "not uploaded")
        print(f"\nSuccess! Video: {youtube_url}")
        print(f"Alpha Scores: {result.get('tasks_completed')}")
        sys.exit(0)
