"""HTML-to-text extraction tests. Pure, no network. The live browsing over
Tor is exercised by demo/browse_demo.py."""
from toragents.browse import html_to_text, Page


SAMPLE = """
<html><head><title>  The Corner </title>
<style>.x{color:red}</style></head>
<body>
  <h1>Welcome, agents</h1>
  <p>This is an <a href="/about">anonymous</a> place.</p>
  <script>track(everything)</script>
  <p>No one is <a href="https://ex.it/watch">watching</a>.</p>
</body></html>
"""


def test_extracts_title():
    title, _, _ = html_to_text(SAMPLE)
    assert title == "The Corner"


def test_drops_script_and_style():
    _, text, _ = html_to_text(SAMPLE)
    assert "track" not in text
    assert "color:red" not in text


def test_keeps_readable_text():
    _, text, _ = html_to_text(SAMPLE)
    assert "Welcome, agents" in text
    assert "anonymous" in text
    assert "No one is" in text


def test_collects_links():
    _, _, links = html_to_text(SAMPLE)
    assert "/about" in links
    assert "https://ex.it/watch" in links


def test_block_tags_prevent_word_runon():
    _, text, _ = html_to_text("<p>alpha</p><p>beta</p>")
    assert "alphabeta" not in text
    assert "alpha" in text and "beta" in text


def test_page_summary_truncates():
    page = Page(
        url="http://x.onion", status=200, content_type="text/html",
        html="", title="T", text="x" * 5000,
    )
    s = page.summary(limit=100)
    assert "more chars" in s
    assert s.count("x") <= 200  # not the whole 5000
