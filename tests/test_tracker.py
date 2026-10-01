import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ki_news_tracker as t  # noqa: E402

RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel><title>X</title>
<item><title>Neues Modell &amp; mehr</title><atom:link href="https://ex.com/ignored"/>
<link>https://www.ex.com/news/a/?utm_source=rss</link><guid>a</guid>
<description>&lt;p&gt;Hallo &lt;b&gt;Welt&lt;/b&gt;&lt;/p&gt;</description>
<pubDate>Thu, 01 Oct 2026 10:00:00 +0000</pubDate></item>
<item><title>Alt</title><link>https://ex.com/news/b</link><pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = b"""  <?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Y</title>
<entry><title>Atom-Post</title><link rel="self" href="https://y.com/self"/>
<link rel="alternate" href="https://y.com/post"/><id>tag:y,1</id>
<updated>2026-09-30T08:00:00Z</updated><summary>Kurz</summary></entry></feed>"""


class ParseTests(unittest.TestCase):
    def test_rss(self):
        items = t.parse_feed(RSS, "X")
        self.assertEqual(len(items), 2)
        a = items[0]
        self.assertEqual(a.title, "Neues Modell & mehr")
        self.assertEqual(a.link, "https://www.ex.com/news/a/?utm_source=rss")
        self.assertEqual(a.key, "ex.com/news/a")
        self.assertEqual(a.summary, "Hallo Welt")
        self.assertEqual(a.published, "2026-10-01T10:00:00+00:00")

    def test_atom(self):
        (item,) = t.parse_feed(ATOM, "Y")
        self.assertEqual(item.link, "https://y.com/post")
        self.assertEqual(item.published, "2026-09-30T08:00:00+00:00")
        self.assertEqual(item.summary, "Kurz")


    def test_fragment_links_stay_distinct(self):
        a = t.normalize_key("https://code.claude.com/docs/en/changelog#2-1-1")
        b = t.normalize_key("https://code.claude.com/docs/en/changelog#2-1-2")
        self.assertNotEqual(a, b)


class RunTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        cfg = {"settings": {"max_age_days": 3650}, "sources": [{"name": "X", "url": "https://x/feed"}]}
        (self.tmp / "sources.json").write_text(json.dumps(cfg))
        self.patches = [
            mock.patch.object(t, "CONFIG_FILE", self.tmp / "sources.json"),
            mock.patch.object(t, "STATE_FILE", self.tmp / "data" / "state.json"),
            mock.patch.object(t, "NEWS_MD", self.tmp / "NEWS.md"),
        ]
        for p in self.patches:
            p.start()
        self.feed = RSS

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _run(self, sent):
        with mock.patch.object(t, "http_request", lambda url, *a, **k: self.feed), \
             mock.patch.object(t.Notifier, "send_item", lambda self_, it: sent.append(it.title) or True), \
             mock.patch.dict("os.environ", {"NTFY_TOPIC": "x"}, clear=True):
            return t.run()

    def test_first_run_silent_then_only_new(self):
        sent = []
        self.assertEqual(self._run(sent), 0)
        self.assertEqual(sent, [])  # erster Lauf: keine Benachrichtigung
        self.assertIn("Neues Modell", (self.tmp / "NEWS.md").read_text())

        self.assertEqual(self._run(sent), 0)
        self.assertEqual(sent, [])  # nichts Neues

        self.feed = RSS.replace(b"<channel><title>X</title>",
                                b"<channel><title>X</title><item><title>Ganz neu</title>"
                                b"<link>https://ex.com/news/c</link></item>")
        self._run(sent)
        self.assertEqual(sent, ["Ganz neu"])
        self._run(sent)
        self.assertEqual(sent, ["Ganz neu"])  # nicht doppelt

    def test_failed_delivery_is_retried(self):
        self._run([])
        self.feed = RSS.replace(b"<channel><title>X</title>",
                                b"<channel><title>X</title><item><title>Retry</title>"
                                b"<link>https://ex.com/news/r</link></item>")
        with mock.patch.object(t, "http_request", lambda url, *a, **k: self.feed), \
             mock.patch.object(t.Notifier, "send_item", lambda self_, it: False), \
             mock.patch.dict("os.environ", {"NTFY_TOPIC": "x"}, clear=True):
            t.run()
        sent = []
        self._run(sent)
        self.assertEqual(sent, ["Retry"])


if __name__ == "__main__":
    unittest.main()
