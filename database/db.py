"""Database Module — MySQL storage for all pipeline data.

Tables:
- daily_runs: Pipeline execution tracking
- trending_stocks: StockTwits trending data
- technical_analysis: Technical analysis data per ticker per day
- alpha_scores: Alpha Score results per ticker per day
- catalysts: News/catalyst data per ticker per day
- videos: Video metadata and YouTube upload info
"""
import json
import logging
from datetime import datetime
from pathlib import Path

import mysql.connector
from mysql.connector import Error as MySQLError

from config.settings import (
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE,
    DATA_DIR,
)

logger = logging.getLogger("alpha_trading_floor")


def get_connection():
    """Get a MySQL connection."""
    try:
        conn = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE,
            autocommit=True,
        )
        return conn
    except MySQLError as e:
        logger.error(f"MySQL connection failed: {e}")
        return None


def init_database():
    """Create all tables if they don't exist."""
    conn = get_connection()
    if not conn:
        logger.warning("MySQL not available — data will be saved to JSON files only")
        return False

    cursor = conn.cursor()

    # daily_runs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_runs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            run_date DATE NOT NULL,
            mode VARCHAR(20) NOT NULL,
            status VARCHAR(30) NOT NULL,
            duration_seconds FLOAT,
            errors TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY idx_run_date (run_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # trending_stocks
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trending_stocks (
            id INT AUTO_INCREMENT PRIMARY KEY,
            run_date DATE NOT NULL,
            symbol VARCHAR(10) NOT NULL,
            title VARCHAR(200),
            watchlist_count INT,
            bullish_count INT,
            bearish_count INT,
            message_count INT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            KEY idx_date_symbol (run_date, symbol)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # technical_analysis
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS technical_analysis (
            id INT AUTO_INCREMENT PRIMARY KEY,
            run_date DATE NOT NULL,
            symbol VARCHAR(10) NOT NULL,
            current_price DECIMAL(12,2),
            change_pct DECIMAL(8,2),
            change_5d_pct DECIMAL(8,2),
            change_20d_pct DECIMAL(8,2),
            rsi_14 DECIMAL(8,2),
            sma_20 DECIMAL(12,2),
            sma_50 DECIMAL(12,2),
            sma_200 DECIMAL(12,2),
            ema_9 DECIMAL(12,2),
            ema_21 DECIMAL(12,2),
            ema_50 DECIMAL(12,2),
            vwap DECIMAL(12,2),
            relative_volume DECIMAL(8,2),
            volume BIGINT,
            avg_volume_20 BIGINT,
            supports JSON,
            resistances JSON,
            signals JSON,
            ema_alignment VARCHAR(20),
            vwap_position VARCHAR(20),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY idx_date_symbol (run_date, symbol)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # alpha_scores
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alpha_scores (
            id INT AUTO_INCREMENT PRIMARY KEY,
            run_date DATE NOT NULL,
            symbol VARCHAR(10) NOT NULL,
            alpha_score DECIMAL(8,2) NOT NULL,
            classification VARCHAR(20) NOT NULL,
            momentum DECIMAL(8,2),
            relative_volume DECIMAL(8,2),
            technical_structure DECIMAL(8,2),
            catalyst_strength DECIMAL(8,2),
            sentiment DECIMAL(8,2),
            risk_quality DECIMAL(8,2),
            raw_score DECIMAL(8,2),
            risk_penalties DECIMAL(8,2),
            penalty_details JSON,
            signals JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY idx_date_symbol (run_date, symbol),
            KEY idx_score (alpha_score DESC)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # catalysts
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS catalysts (
            id INT AUTO_INCREMENT PRIMARY KEY,
            run_date DATE NOT NULL,
            symbol VARCHAR(10) NOT NULL,
            category VARCHAR(30) NOT NULL,
            strength DECIMAL(4,1),
            confidence DECIMAL(4,2),
            headline TEXT,
            sentiment DECIMAL(4,2),
            source VARCHAR(100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            KEY idx_date_symbol (run_date, symbol)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # videos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            run_date DATE NOT NULL,
            title VARCHAR(200),
            video_path VARCHAR(500),
            audio_path VARCHAR(500),
            youtube_id VARCHAR(20),
            youtube_url VARCHAR(200),
            script_hash VARCHAR(32),
            word_count INT,
            mode VARCHAR(20),
            status VARCHAR(30),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY idx_run_date (run_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # content_performance — per-video/channel metrics over time
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS content_performance (
            id INT AUTO_INCREMENT PRIMARY KEY,
            platform VARCHAR(20) NOT NULL,
            content_id VARCHAR(50) NOT NULL,
            content_url VARCHAR(500),
            title VARCHAR(300),
            publish_date DATE,
            ticker_mentions JSON,
            format_tag VARCHAR(50),
            experiment_id VARCHAR(50),
            views INT DEFAULT 0,
            likes INT DEFAULT 0,
            comments INT DEFAULT 0,
            shares INT DEFAULT 0,
            subscribers_gained INT,
            impressions INT,
            ctr DECIMAL(8,4),
            estimated_minutes_watched FLOAT,
            avg_view_duration FLOAT,
            avg_view_pct FLOAT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            KEY idx_platform_content (platform, content_id),
            KEY idx_publish_date (publish_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # content_insights — derived patterns and recommendations
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS content_insights (
            id INT AUTO_INCREMENT PRIMARY KEY,
            insight_date DATE NOT NULL,
            insight_type VARCHAR(50) NOT NULL,
            key_finding TEXT NOT NULL,
            metric_value FLOAT,
            sample_size INT,
            confidence DECIMAL(4,2),
            recommendation TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY idx_insight_date_type (insight_date, insight_type)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # content_experiments — A/B test tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS content_experiments (
            id INT AUTO_INCREMENT PRIMARY KEY,
            experiment_id VARCHAR(50) NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE,
            variable_tested VARCHAR(100) NOT NULL,
            control_value TEXT,
            experiment_value TEXT,
            control_metric FLOAT,
            experiment_metric FLOAT,
            sample_size INT DEFAULT 0,
            status VARCHAR(20) DEFAULT 'running',
            winner VARCHAR(20),
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY idx_experiment_id (experiment_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # upcoming_events — verified future events for script hooks
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS upcoming_events (
            id INT AUTO_INCREMENT PRIMARY KEY,
            ticker VARCHAR(10),
            event_type VARCHAR(50) NOT NULL,
            event_date DATE NOT NULL,
            event_time VARCHAR(20),
            source VARCHAR(200),
            confidence DECIMAL(4,2) DEFAULT 1.0,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            KEY idx_event_date (event_date),
            KEY idx_ticker (ticker)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # Lightweight schema migrations for tables created by older versions.
    for migration in (
        "ALTER TABLE daily_runs MODIFY status VARCHAR(30) NOT NULL",
        "ALTER TABLE videos MODIFY status VARCHAR(30)",
        "ALTER TABLE content_performance ADD COLUMN estimated_minutes_watched FLOAT AFTER ctr",
    ):
        try:
            cursor.execute(migration)
        except MySQLError as e:
            logger.warning("Schema migration skipped: %s", e)

    cursor.close()
    conn.close()

    logger.info("Database tables initialized")
    return True


def save_run(status: dict):
    """Save pipeline run status to database and JSON."""
    today = datetime.now().strftime("%Y-%m-%d")
    json_path = DATA_DIR / f"run_{today}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w") as f:
        json.dump(status, f, indent=2, default=str)

    conn = get_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO daily_runs (run_date, mode, status, duration_seconds, errors)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE mode=%s, status=%s, duration_seconds=%s, errors=%s
        """, (
            today, status.get("mode", ""), status.get("status", ""),
            status.get("duration_seconds", 0),
            json.dumps(status.get("errors", [])),
            status.get("mode", ""), status.get("status", ""),
            status.get("duration_seconds", 0),
            json.dumps(status.get("errors", [])),
        ))
        cursor.close()
    except Exception as e:
        logger.warning(f"Failed to save run to DB: {e}")
    finally:
        conn.close()


def save_trending(trending: list):
    """Save StockTwits trending data."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        for t in trending:
            cursor.execute("""
                INSERT INTO trending_stocks (run_date, symbol, title, watchlist_count, bullish_count, bearish_count, message_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                today, t.get("symbol", ""), t.get("title", ""),
                t.get("watchlist_count", 0), t.get("bullish_count", 0),
                t.get("bearish_count", 0), t.get("message_count", 0),
            ))
        cursor.close()
    except Exception as e:
        logger.warning(f"Failed to save trending to DB: {e}")
    finally:
        conn.close()


def save_technical_analysis(technicals: dict):
    """Save technical analysis data for all tickers."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        for ticker, t in technicals.items():
            cursor.execute("""
                INSERT INTO technical_analysis (
                    run_date, symbol, current_price, change_pct, change_5d_pct, change_20d_pct,
                    rsi_14, sma_20, sma_50, sma_200, ema_9, ema_21, ema_50,
                    vwap, relative_volume, volume, avg_volume_20,
                    supports, resistances, signals, ema_alignment, vwap_position
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    current_price=VALUES(current_price), rsi_14=VALUES(rsi_14),
                    signals=VALUES(signals), ema_alignment=VALUES(ema_alignment)
            """, (
                today, ticker,
                t.get("price"), t.get("change_pct"), t.get("change_5d_pct"), t.get("change_20d_pct"),
                t.get("rsi_14"), t.get("sma_20"), t.get("sma_50"), t.get("sma_200"),
                t.get("ema_9"), t.get("ema_21"), t.get("ema_50"),
                t.get("vwap"), t.get("relative_volume"), t.get("volume"), t.get("avg_volume_20"),
                json.dumps(t.get("supports", [])), json.dumps(t.get("resistances", [])),
                json.dumps(t.get("signals", [])), t.get("ema_alignment"), t.get("vwap_position"),
            ))
        cursor.close()
    except Exception as e:
        logger.warning(f"Failed to save technicals to DB: {e}")
    finally:
        conn.close()


def save_alpha_scores(alpha_scores: dict):
    """Save Alpha Score results for all tickers."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        for ticker, s in alpha_scores.items():
            sub = s.get("sub_scores", {})
            cursor.execute("""
                INSERT INTO alpha_scores (
                    run_date, symbol, alpha_score, classification,
                    momentum, relative_volume, technical_structure,
                    catalyst_strength, sentiment, risk_quality,
                    raw_score, risk_penalties, penalty_details, signals
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    alpha_score=VALUES(alpha_score), classification=VALUES(classification)
            """, (
                today, ticker, s.get("alpha_score"), s.get("classification"),
                sub.get("momentum"), sub.get("relative_volume"), sub.get("technical_structure"),
                sub.get("catalyst_strength"), sub.get("sentiment"), sub.get("risk_quality"),
                s.get("raw_score"), s.get("risk_penalties"),
                json.dumps(s.get("penalty_details", [])),
                json.dumps(s.get("signals", [])),
            ))
        cursor.close()
    except Exception as e:
        logger.warning(f"Failed to save alpha scores to DB: {e}")
    finally:
        conn.close()


def save_catalysts(catalysts: dict):
    """Save catalyst data for all tickers."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        for ticker, c in catalysts.items():
            cursor.execute("""
                INSERT INTO catalysts (run_date, symbol, category, strength, confidence, headline, sentiment, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                today, ticker, c.get("category", ""), c.get("strength", 0),
                c.get("confidence", 0), c.get("headline", ""),
                c.get("sentiment", 0), c.get("source", ""),
            ))
        cursor.close()
    except Exception as e:
        logger.warning(f"Failed to save catalysts to DB: {e}")
    finally:
        conn.close()


def save_video(video_data: dict):
    """Save video metadata to database."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO videos (run_date, title, video_path, audio_path, youtube_id, youtube_url,
                              script_hash, word_count, mode, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                title=VALUES(title), youtube_id=VALUES(youtube_id), status=VALUES(status)
        """, (
            today, video_data.get("title", ""),
            video_data.get("video_path", ""), video_data.get("audio_path", ""),
            video_data.get("youtube_id", ""), video_data.get("youtube_url", ""),
            video_data.get("script_hash", ""), video_data.get("word_count", 0),
            video_data.get("mode", ""), video_data.get("status", ""),
        ))
        cursor.close()
    except Exception as e:
        logger.warning(f"Failed to save video to DB: {e}")
    finally:
        conn.close()
