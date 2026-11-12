#!/usr/bin/env python3
"""Alpha Trading Floor — Active Preflight + Auto-Fix

Runs before the daily pipeline and during the social health loop. It checks
critical dependencies and immediately applies bounded repairs where safe:
OAuth refresh, local service restart, LaunchAgent loading, missing dependency
installation, and paused-job recovery. It exits 1 only when a critical issue
remains after repair attempts.

Checks:
  1. YouTube OAuth token validity and proactive refresh
  2. Google API connectivity
  3. Hermes Grok/GPT provider readiness and social LaunchAgents
  4. Disk space on project volume
  5. Video output directory writable
  6. Python venv and critical dependencies
  7. Database connectivity
  8. Market data API reachability (yfinance)
  9. Background music file present
 10. Previous run errors and scheduler state

`--no-fix` is an explicit diagnostic override. Normal and scheduled runs are
always repair-enabled.
"""
import argparse
import json
import os
import sys
import shutil
import socket
import subprocess
import time

from datetime import datetime, timezone
from pathlib import Path

# ─── Add venv site-packages so google.oauth2 etc. are importable ────────
PROJECT_DIR = Path(os.getenv("ALPHA_YOUTUBE_PROJECT_DIR", str(Path(__file__).resolve().parent))).expanduser()
VENV_DIR = PROJECT_DIR / "venv"
_VENV_SITE = VENV_DIR / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
if _VENV_SITE.is_dir() and str(_VENV_SITE) not in sys.path:
    sys.path.insert(0, str(_VENV_SITE))
TOKEN_PATH = Path(os.getenv("GOOGLE_TOKEN_PATH", str(Path.home() / "money-machines" / "shared" / ".google_token.json"))).expanduser()
LOGS_DIR = PROJECT_DIR / "logs"
VIDEOS_DIR = PROJECT_DIR / "videos"
MUSIC_DIR = PROJECT_DIR / "music"
DATA_DIR = PROJECT_DIR / "data"
CONFIG_DIR = PROJECT_DIR / "config"

CRITICAL_FAILURES = 0
WARNINGS = 0
AUTO_FIX = False
FIX_ACTIONS: list[str] = []
FIX_FAILURES: list[str] = []


def _run_repair(label: str, command: list[str], *, timeout: int = 90) -> bool:
    """Run one bounded, known repair and record the result without secrets."""
    if not AUTO_FIX:
        print(f"↪️  AUTO-FIX SKIPPED: {label} (explicit --no-fix)")
        return False
    print(f"🔧 AUTO-FIX: {label}")
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    except Exception as exc:
        FIX_FAILURES.append(f"{label}: {type(exc).__name__}")
        print(f"❌ AUTO-FIX FAILED: {label}: {type(exc).__name__}")
        return False
    if result.returncode == 0:
        FIX_ACTIONS.append(label)
        print(f"✅ AUTO-FIX APPLIED: {label}")
        return True
    FIX_FAILURES.append(f"{label}: exit {result.returncode}")
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    # Never print credential-bearing command output verbatim.
    safe_detail = detail[-1][:240] if detail else "no diagnostic output"
    for secret_name in ("access_token", "refresh_token", "client_secret", "token"):
        safe_detail = safe_detail.replace(secret_name, "[REDACTED]")
    print(f"❌ AUTO-FIX FAILED: {label}: {safe_detail}")
    return False


def _launchctl_domain() -> str:
    return f"gui/{os.getuid()}"


def _launchagent_loaded(label: str) -> bool:
    result = subprocess.run(["launchctl", "print", f"{_launchctl_domain()}/{label}"],
                            capture_output=True, text=True, timeout=15)
    return result.returncode == 0


def _ensure_launchagent(label: str, plist: Path, *, restart: bool = False) -> bool:
    """Load a missing LaunchAgent and optionally restart a dependency."""
    if not plist.exists():
        print(f"❌ AUTO-FIX BLOCKED: LaunchAgent plist missing: {plist}")
        FIX_FAILURES.append(f"{label}: plist missing")
        return False
    if not _launchagent_loaded(label):
        _run_repair(f"load LaunchAgent {label}", ["launchctl", "load", "-w", str(plist)])
    if restart and _launchagent_loaded(label):
        _run_repair(f"restart LaunchAgent {label}",
                    ["launchctl", "kickstart", "-k", f"{_launchctl_domain()}/{label}"])
    return _launchagent_loaded(label)


def fail(msg: str):
    global CRITICAL_FAILURES
    CRITICAL_FAILURES += 1
    print(f"❌ CRITICAL: {msg}")


def warn(msg: str):
    global WARNINGS
    WARNINGS += 1
    print(f"⚠️  WARNING: {msg}")


def ok(msg: str):
    print(f"✅ {msg}")


def check_youtube_oauth():
    """Check 1: YouTube OAuth token validity and scope."""
    print("\n=== 1. YouTube OAuth Token ===")

    if not TOKEN_PATH.exists():
        fail("Google token file missing: {TOKEN_PATH}. Re-authorize with: "
             "cd ~/money-machines && source .venv/bin/activate && "
             "python3 shared/scripts/google_oauth_setup.py")
        return

    try:
        data = json.loads(TOKEN_PATH.read_text())
    except (json.JSONDecodeError, ValueError) as e:
        fail(f"Token file is corrupt: {e}. Re-authorize with google_oauth_setup.py")
        return

    expiry_str = data.get("expiry", "")
    has_refresh = bool(data.get("refresh_token"))
    scopes = data.get("scopes", [])

    if not expiry_str:
        fail("Token file has no expiry field. Re-authorize.")
        return

    try:
        expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        fail(f"Token expiry is not parseable: {expiry_str}. Re-authorize.")
        return

    now = datetime.now(timezone.utc)
    time_left = expiry - now
    hours_left = time_left.total_seconds() / 3600
    # Refresh before expiry, not only after expiry. This is the key keepalive
    # behavior: a valid short-lived access token must still be refreshed.
    if hours_left <= 4:
        if not has_refresh:
            fail(f"Token expires in {hours_left:.1f} hours and no refresh token is present. Re-authorize.")
            return
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request

            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH))
            if not creds.refresh_token:
                fail("Token is not refreshable. Re-authorize with google_oauth_setup.py")
                return
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())
            FIX_ACTIONS.append("refresh YouTube OAuth token")
            ok(f"YouTube OAuth token auto-refreshed. New expiry: {creds.expiry}")
        except Exception as e:
            error_msg = str(e)
            if "invalid_grant" in error_msg:
                fail("Token refresh failed: invalid_grant (refresh token revoked). "
                     "Re-authorize with google_oauth_setup.py")
            else:
                fail(f"Token refresh failed: {type(e).__name__}. Re-authorize if it persists.")
            return
    elif hours_left < 24:
        warn(f"Token expires in {hours_left:.1f} hours ({expiry_str}); outside refresh margin.")
    else:
        ok(f"Token valid. Expiry: {expiry_str} ({hours_left:.0f}h remaining)")


    # Check scopes
    required_scopes = {"https://www.googleapis.com/auth/youtube.upload"}
    missing = required_scopes - set(scopes)
    if missing:
        fail(f"Missing required scopes: {missing}. Re-authorize with google_oauth_setup.py")
    else:
        ok(f"Scopes: {', '.join(scopes)}")

    if "https://www.googleapis.com/auth/youtube.readonly" not in scopes:
        warn("Missing youtube.readonly scope — analytics ingest will fail. "
             "Re-authorize with google_oauth_setup.py to add it.")


def check_api_connectivity():
    """Check 2: Google API and market data endpoints reachable."""
    print("\n=== 2. API Connectivity ===")

    endpoints = [
        ("oauth2.googleapis.com", 443, "Google OAuth"),
        ("www.googleapis.com", 443, "YouTube Data API"),
        ("query1.finance.yahoo.com", 443, "Yahoo Finance (yfinance)"),
        ("chatgpt.com", 443, "Codex LLM"),
        ("api.stocktwits.com", 443, "StockTwits"),
        ("finviz.com", 443, "Finviz"),
    ]

    for host, port, label in endpoints:
        try:
            sock = socket.create_connection((host, port), timeout=5)
            sock.close()
            ok(f"{label} ({host}:{port})")
        except (socket.timeout, OSError) as e:
            warn(f"{label} ({host}:{port}) unreachable: {e}")


def check_hermes_provider():
    """Check Hermes configuration and authenticated Grok/GPT credentials."""
    print("\n=== 2b. Hermes Grok/GPT Provider ===")
    hermes_bin = os.getenv("HERMES_BIN", "").strip() or shutil.which("hermes") or "hermes"
    if not Path(hermes_bin).exists():
        fail("Hermes CLI is not installed at the configured path")
        return

    result = subprocess.run(
        [hermes_bin, "config", "check"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        fail("Hermes configuration check failed")
        return

    auth_path = Path.home() / ".hermes" / "auth.json"
    if not auth_path.exists():
        fail("Hermes auth file is missing; Grok/GPT OAuth cannot be verified")
        return
    try:
        auth = json.loads(auth_path.read_text())
        providers = set(auth.get("providers", {})) | set(auth.get("credential_pool", {}))
    except Exception:
        providers = set()
    available = sorted(providers & {"xai-oauth", "openai-codex"})
    if not available:
        fail("Hermes has no configured Grok/GPT OAuth provider")
        return
    ok("Hermes configuration valid; authenticated provider(s): " + ", ".join(available))


def check_social_launchagents():
    """Load missing social LaunchAgents without triggering public posts."""
    print("\n=== 2c. Social LaunchAgents ===")
    agents = {
        "com.jarvis.nontrading.inspiration-text": Path.home() / "Library" / "LaunchAgents" / "com.jarvis.nontrading.inspiration-text.plist",
        "com.jarvis.nontrading.inspiration-reels": Path.home() / "Library" / "LaunchAgents" / "com.jarvis.nontrading.inspiration-reels.plist",
    }
    for label, plist in agents.items():
        if _ensure_launchagent(label, plist):
            ok(f"LaunchAgent loaded: {label}")
        else:
            fail(f"LaunchAgent unavailable after auto-fix: {label}")


def check_disk_space():
    """Check 3: Disk space on project volume."""
    print("\n=== 3. Disk Space ===")

    try:
        usage = shutil.disk_usage(PROJECT_DIR)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        pct = (usage.used / usage.total) * 100

        if free_gb < 5:
            fail(f"Only {free_gb:.1f} GB free ({pct:.0f}% used). Pipeline needs ~1 GB/video.")
        elif free_gb < 20:
            warn(f"Only {free_gb:.1f} GB free ({pct:.0f}% used). Consider cleaning up old videos.")
        else:
            ok(f"{free_gb:.1f} GB free / {total_gb:.0f} GB total ({pct:.0f}% used)")
    except Exception as e:
        warn(f"Could not check disk space: {e}")


def check_output_dirs():
    """Check 4: Video output directories writable."""
    print("\n=== 4. Output Directories ===")

    today = datetime.now().strftime("%Y-%m-%d")
    for d in [VIDEOS_DIR / today, LOGS_DIR, DATA_DIR]:
        d.mkdir(parents=True, exist_ok=True)
        test_file = d / ".preflight_test"
        try:
            test_file.write_text("ok")
            test_file.unlink()
            ok(f"{d} writable")
        except Exception as e:
            fail(f"{d} not writable: {e}")


def check_venv_and_deps():
    """Check 5: Python venv and critical dependencies."""
    print("\n=== 5. Python Environment ===")

    venv_python = VENV_DIR / "bin" / "python3"
    if not venv_python.exists():
        fail(f"Venv Python not found: {venv_python}")
        return

    ok(f"Venv Python: {venv_python}")

    # Check critical imports inside the project venv (with PYTHONPATH set)
    critical_modules = [
        ("yfinance", "yfinance"),
        ("googleapiclient", "googleapiclient"),
        ("google.oauth2.credentials", "google-auth"),
        ("httpx", "httpx"),
        ("PIL", "Pillow"),
    ]

    env = {**os.environ, "PYTHONPATH": f"{PROJECT_DIR}:{PROJECT_DIR / 'app'}:{PROJECT_DIR / 'config'}"}

    for module_name, pip_name in critical_modules:
        result = subprocess.run(
            [str(venv_python), "-c", f"import {module_name}"],
            capture_output=True, text=True, timeout=90,
            env=env,
        )
        if result.returncode != 0:
            repaired = _run_repair(
                f"install missing dependency {pip_name}",
                [str(venv_python), "-m", "pip", "install", "--disable-pip-version-check", pip_name],
                timeout=300,
            )
            verify = subprocess.run(
                [str(venv_python), "-c", f"import {module_name}"],
                capture_output=True, text=True, timeout=90, env=env,
            ) if repaired else None
            if verify is not None and verify.returncode == 0:
                ok(f"Dependency repaired: {pip_name}")
            else:
                fail(f"Missing dependency: {pip_name} (import {module_name} failed)")
        else:
            ok(f"Dependency: {pip_name}")

    # Check ffmpeg (used for video composition — no Python package, it's a binary)
    if shutil.which("ffmpeg"):
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        version_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
        ok(f"ffmpeg: {version_line}")
    else:
        fail("ffmpeg not found in PATH — video composition will fail")


def check_database():
    """Check 6: Database connectivity."""
    print("\n=== 6. Database ===")

    try:
        sys.path.insert(0, str(PROJECT_DIR))
        sys.path.insert(0, str(PROJECT_DIR / "app"))
        sys.path.insert(0, str(PROJECT_DIR / "config"))
        from database.db import init_database, get_connection
        init_database()
        conn = get_connection()
        if conn:
            ok("Database connection OK")
            conn.close()
        else:
            warn("Database returned None — pipeline will fall back to JSON")
    except ImportError:
        warn("Database module not importable — may fall back to JSON")
    except Exception as e:
        warn(f"Database check failed: {e} — pipeline may fall back to JSON")


def check_market_data():
    """Check 7: Market data API reachable (quick yfinance test)."""
    print("\n=== 7. Market Data API ===")

    venv_python = VENV_DIR / "bin" / "python3"
    if not venv_python.exists():
        fail("Venv Python not found — skipping market data check")
        return

    result = subprocess.run(
        [str(venv_python), "-c",
         "import yfinance as yf; "
         "t = yf.Ticker('SPY'); "
         "h = t.history(period='1d'); "
         "print('SPY close:', h['Close'].iloc[-1] if len(h) > 0 else 'NO_DATA')"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": str(PROJECT_DIR)}
    )

    if result.returncode == 0 and "NO_DATA" not in result.stdout:
        ok(f"Market data API: {result.stdout.strip()}")
    elif "NO_DATA" in result.stdout:
        warn("yfinance returned no data (market may be closed)")
    else:
        warn(f"Market data check failed: {result.stderr.strip()[:200]}")


def check_background_music():
    """Check 8: Background music file present."""
    print("\n=== 8. Background Music ===")

    music_files = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.wav"))
    if music_files:
        ok(f"Found {len(music_files)} music file(s): {[f.name for f in music_files]}")
    else:
        fail("No background music files found in music/ directory")


def check_previous_errors():
    """Check 9: Check last run for recurring errors."""
    print("\n=== 9. Last Run Status ===")

    status_path = LOGS_DIR / "last_run_status.json"
    if not status_path.exists():
        warn("No last_run_status.json found")
        return

    try:
        status = json.loads(status_path.read_text())
        errors = status.get("errors", [])
        if errors:
            for e in errors:
                err_str = str(e)
                if "invalid_grant" in err_str:
                    # Check if token has been refreshed since the error
                    if TOKEN_PATH.exists():
                        try:
                            token_data = json.loads(TOKEN_PATH.read_text())
                            token_expiry = token_data.get("expiry", "")
                            if token_expiry:
                                from datetime import timezone as _tz
                                exp = datetime.fromisoformat(token_expiry.replace("Z", "+00:00"))
                                if exp > datetime.now(_tz.utc):
                                    warn(f"Previous run had OAuth error, but token is now refreshed (expiry: {token_expiry})")
                                else:
                                    fail(f"Previous run had OAuth error AND token is still expired. Re-authorize!")
                        except Exception:
                            warn(f"Previous run had OAuth error. Verify token is current.")
                    else:
                        fail(f"Previous run had OAuth error AND no token file exists. Re-authorize!")
                else:
                    warn(f"Previous run had error: {e}")
        else:
            ok(f"Previous run: no errors (status: {status.get('status', 'unknown')})")

        # Check age of last run
        started = status.get("started_at", "")
        if started:
            try:
                start_dt = datetime.fromisoformat(started)
                age_hours = (datetime.now(start_dt.tzinfo) - start_dt).total_seconds() / 3600
                if age_hours > 36:
                    warn(f"Last run was {age_hours:.0f}h ago — may be stale")
                else:
                    ok(f"Last run: {age_hours:.1f}h ago")
            except Exception:
                pass
    except Exception as e:
        warn(f"Could not parse last run status: {e}")


def check_cron_schedule():
    """Check 10: Verify cron job is scheduled and enabled."""
    print("\n=== 10. Cron Schedule ===")

    result = subprocess.run(
        ["hermes", "cron", "list", "--all"],
        capture_output=True, text=True, timeout=15
    )

    if "745ca5d2266b" in result.stdout:
        # Check if enabled
        for line in result.stdout.split("\n"):
            if "745ca5d2266b" in line or "Daily Video" in line:
                if "paused" in line.lower() or "disabled" in line.lower():
                    if _run_repair("resume Alpha Trading Floor daily video cron", ["hermes", "cron", "resume", "745ca5d2266b"]):
                        verify = subprocess.run(
                            ["hermes", "cron", "list", "--all"],
                            capture_output=True, text=True, timeout=15,
                        )
                        if "745ca5d2266b [active]" in verify.stdout:
                            ok("Daily video cron resumed automatically")
                        else:
                            fail("Daily video cron still appears paused after auto-fix")
                    else:
                        fail(f"Cron job appears paused/disabled: {line.strip()}")
                else:
                    ok(f"Cron job found and active: {line.strip()[:100]}")
                break
    else:
        warn("Could not verify cron schedule via hermes CLI")


# ─── Main ──────────────────────────────────────────────────────────────────
def main():
    global AUTO_FIX
    parser = argparse.ArgumentParser(description="Alpha Trading Floor active preflight and repair")
    parser.add_argument("--fix", action="store_true", help="explicitly enable bounded auto-fixes")
    parser.add_argument("--no-fix", action="store_true", help="diagnostic override; do not repair anything")
    args = parser.parse_args()
    AUTO_FIX = bool(args.fix and not args.no_fix)

    print("=" * 60)
    print("  ALPHA TRADING FLOOR — PREFLIGHT + AUTO-FIX")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  AUTO-FIX: {'ENABLED' if AUTO_FIX else 'DISABLED BY EXPLICIT OVERRIDE'}")
    print("=" * 60)

    check_youtube_oauth()
    check_api_connectivity()
    check_hermes_provider()
    check_social_launchagents()
    check_disk_space()
    check_output_dirs()
    check_venv_and_deps()
    check_database()
    check_market_data()
    check_background_music()
    check_previous_errors()
    check_cron_schedule()

    print("\n" + "=" * 60)
    if FIX_ACTIONS:
        print("  AUTO-FIX ACTIONS: " + "; ".join(FIX_ACTIONS))
    else:
        print("  AUTO-FIX ACTIONS: none needed")
    if FIX_FAILURES:
        print("  AUTO-FIX FAILURES: " + "; ".join(FIX_FAILURES))
    if CRITICAL_FAILURES > 0:
        print(f"  PREFLIGHT FAILED: {CRITICAL_FAILURES} critical, {WARNINGS} warnings")
        print("=" * 60)
        sys.exit(1)
    elif WARNINGS > 0 or FIX_FAILURES:
        print(f"  PREFLIGHT PASSED (with {WARNINGS} warnings; repairs verified where possible)")
        print("=" * 60)
        sys.exit(0)
    else:
        print("  PREFLIGHT PASSED — all checks OK")
        print("=" * 60)
        sys.exit(0)


if __name__ == "__main__":
    main()
