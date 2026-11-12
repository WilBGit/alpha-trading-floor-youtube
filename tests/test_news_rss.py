import unittest
from datetime import datetime, timedelta, timezone

from collectors.news_rss import _matches_ticker, dedupe_news_items, parse_feed


class NewsRssTests(unittest.TestCase):
    def test_parse_feed_preserves_publisher_and_rejects_stale_items(self):
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=2)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        stale = (now - timedelta(days=10)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        xml = f"""<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <item><title>Fed signals rates will remain steady</title>
            <link>https://example.com/recent</link><pubDate>{recent}</pubDate>
            <source>Reuters</source><description>Policy update</description></item>
          <item><title>Old market story</title>
            <link>https://example.com/old</link><pubDate>{stale}</pubDate></item>
          <item><title>Major Economic Indicators Latest Numbers</title>
            <link>https://example.com/index</link><pubDate>{recent}</pubDate></item>
        </channel></rss>"""
        items = parse_feed(xml, fallback_source="Test Feed", max_age_hours=96, now=now)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "Reuters")
        self.assertEqual(items[0]["url"], "https://example.com/recent")
        self.assertTrue(items[0]["date"].endswith("+00:00"))

    def test_parse_atom_feed(self):
        now = datetime.now(timezone.utc)
        published = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        xml = f"""<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry><title>SEC updates disclosure rule</title>
          <link href="https://example.com/sec"/><published>{published}</published>
          <summary>Primary release</summary></entry>
        </feed>"""
        items = parse_feed(xml, fallback_source="SEC", max_age_hours=96, now=now)
        self.assertEqual(items[0]["headline"], "SEC updates disclosure rule")
        self.assertEqual(items[0]["source"], "SEC")
        self.assertEqual(items[0]["url"], "https://example.com/sec")

    def test_dedupe_collapses_cross_source_headline_variants(self):
        items = [
            {"headline": "Nvidia shares rise after earnings beat", "source": "Reuters", "date": "2026-07-29T12:00:00+00:00", "url": "https://a"},
            {"headline": "Nvidia shares rise after earnings beat!", "source": "Yahoo Finance", "date": "2026-07-29T12:05:00+00:00", "url": "https://b"},
            {"headline": "Federal Reserve leaves rates unchanged", "source": "Federal Reserve", "date": "2026-07-29T13:00:00+00:00", "url": "https://c"},
        ]
        result = dedupe_news_items(items)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["headline"], "Federal Reserve leaves rates unchanged")

    def test_index_filter_rejects_unrelated_single_company_story(self):
        unrelated = {"headline": "SoFi falls after earnings", "snippet": "Quarterly company results"}
        macro = {"headline": "Federal Reserve holds interest rates steady", "snippet": "Policy statement"}
        direct = {"headline": "SPY tests a new record high", "snippet": "ETF update"}
        self.assertFalse(_matches_ticker(unrelated, "SPY", "SPDR S&P 500 ETF Trust"))
        self.assertTrue(_matches_ticker(macro, "SPY", "SPDR S&P 500 ETF Trust"))
        self.assertTrue(_matches_ticker(direct, "SPY", "SPDR S&P 500 ETF Trust"))


if __name__ == "__main__":
    unittest.main()
