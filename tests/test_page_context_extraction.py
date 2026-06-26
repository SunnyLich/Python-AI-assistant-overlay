from types import SimpleNamespace

from core import context_fetcher, context_hotkey


def test_duckduckgo_result_parser_extracts_titles_urls_and_snippets():
    html = """
    <div class="result">
      <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.test%2Fnews">Example News</a>
      <a class="result__snippet">Fresh headline summary.</a>
    </div>
    """
    parser = context_fetcher._DuckDuckGoResultParser(5)

    parser.feed(html)
    parser.close()

    assert parser.results == [
        {
            "title": "Example News",
            "url": "https://example.test/news",
            "snippet": "Fresh headline summary.",
        }
    ]


def test_extract_useful_page_context_orders_structured_html():
    html = """
    <html>
      <head>
        <title>Wisp Browser Context</title>
        <meta name="description" content="How Wisp reads useful browser context.">
      </head>
      <body>
        <nav>Home Pricing Login</nav>
        <div class="cookie-banner">Accept all cookies</div>
        <main>
          <h1>Browser context design</h1>
          <h2>Capture model</h2>
          <p>Wisp captures the active browser page and turns it into ordered context.</p>
          <p>The model should see headline content before secondary page details.</p>
          <a href="/docs/context">Context docs</a>
        </main>
        <footer>Legal links and footer text</footer>
      </body>
    </html>
    """

    text = context_fetcher.extract_useful_page_context(
        url="https://example.test/product",
        html=html,
        max_chars=2000,
    )

    assert "Title:\nWisp Browser Context" in text
    assert "Page description:\nHow Wisp reads useful browser context." in text
    assert "Headings:\n- Browser context design\n- Capture model" in text
    assert "Main content:\nBrowser context design" in text
    assert "Wisp captures the active browser page" in text
    assert "Links:\n- Context docs: https://example.test/docs/context" in text
    assert "Accept all cookies" not in text
    assert "Legal links" not in text
    assert "Home Pricing Login" not in text
    assert "Most relevant" not in text
    assert "Additional page text" not in text


def test_extract_useful_page_context_formats_rendered_text_without_relevance_labels():
    rendered = """
    Example Docs

    Overview
    Wisp can read rendered browser text when the browser exposes it.

    Setup
    Enable the browser context mode for the caller.
    Overview
    """

    text = context_fetcher.extract_useful_page_context(
        rendered_text=rendered,
        max_chars=2000,
    )

    assert "Title:\nExample Docs" in text
    assert "Headings:" in text
    assert "- Overview" in text
    assert "- Setup" in text
    assert text.count("- Overview") == 1
    assert "Visible page content:" in text
    assert "Most relevant" not in text
    assert "Additional page text" not in text


def test_extract_useful_page_context_clips_and_redacts_final_output():
    html = """
    <html>
      <head><title>Secrets page</title></head>
      <body>
        <main>
          <h1>Token notes</h1>
          <p>api_key = sk-proj-abcdefghijklmnopqrstuvwxyz1234567890</p>
          <p>This long paragraph should force clipping when the context budget is small.</p>
        </main>
      </body>
    </html>
    """

    text = context_fetcher.extract_useful_page_context(
        url="https://example.test/secrets",
        html=html,
        max_chars=140,
    )

    assert "[API_KEY]" in text
    assert "sk-proj-" not in text
    assert len(text) <= 140


def test_browser_context_text_includes_source_priority(monkeypatch):
    monkeypatch.setattr(
        context_hotkey.context_fetcher,
        "fetch_browser_content_for_window",
        lambda url, hwnd: "Title:\nExample",
    )
    snapshot = SimpleNamespace(
        active_window=SimpleNamespace(
            url="https://example.test/page",
            hwnd=777,
            process_name="chrome.exe",
        ),
        browser_content="",
    )

    text = context_hotkey.browser_context_text(snapshot)

    assert "[Browser/Web]" in text
    assert "Source priority: primary" in text
    assert "URL: https://example.test/page" in text
    assert "Title:\nExample" in text


def test_browser_context_text_omits_empty_browser_block():
    snapshot = SimpleNamespace(
        active_window=SimpleNamespace(url="", hwnd=0, process_name="notepad.exe"),
        browser_content="",
    )

    assert context_hotkey.browser_context_text(snapshot) == ""
