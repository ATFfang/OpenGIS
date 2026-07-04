"""Web tools for the agent: webfetch and websearch.

These are intentionally lightweight and dependency-free. They provide the
model-facing capability and safety envelope; network policy remains controlled
by the runtime environment and permission layer.
"""

from __future__ import annotations

import html
import logging
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from opengis_backend.skills.registry import skill

logger = logging.getLogger(__name__)

_MAX_FETCH_BYTES = 5 * 1024 * 1024
_MAX_SEARCH_BYTES = 512 * 1024
_DEFAULT_TIMEOUT = 30
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 OpenGIS-Agent/1.0"
)


class _MarkdownHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.href_stack: list[str | None] = []
        self.list_depth = 0
        self.in_pre = False
        self._pending_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag in {"script", "style", "noscript", "iframe", "svg"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"h1", "h2", "h3"}:
            self.parts.append("\n" + "#" * int(tag[1]) + " ")
        elif tag in {"p", "div", "section", "article", "br"}:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n" + "  " * self.list_depth + "- ")
        elif tag in {"ul", "ol"}:
            self.list_depth += 1
            self.parts.append("\n")
        elif tag == "pre":
            self.in_pre = True
            self.parts.append("\n```\n")
        elif tag == "a":
            self.href_stack.append(attrs_dict.get("href"))
            self._pending_link_text.append("")

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            if tag in {"script", "style", "noscript", "iframe", "svg"}:
                self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag in {"h1", "h2", "h3", "p", "div", "section", "article"}:
            self.parts.append("\n")
        elif tag in {"ul", "ol"}:
            self.list_depth = max(0, self.list_depth - 1)
            self.parts.append("\n")
        elif tag == "pre":
            self.in_pre = False
            self.parts.append("\n```\n")
        elif tag == "a" and self.href_stack:
            href = self.href_stack.pop()
            text = self._pending_link_text.pop() if self._pending_link_text else ""
            if href and text.strip():
                self.parts.append(f" ({href})")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = data if self.in_pre else re.sub(r"\s+", " ", data)
        if not text.strip() and not self.in_pre:
            return
        if self._pending_link_text:
            self._pending_link_text[-1] += text
        self.parts.append(text)

    def markdown(self) -> str:
        text = "".join(self.parts)
        text = html.unescape(text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _html_to_markdown(value: str) -> str:
    parser = _MarkdownHTMLParser()
    parser.feed(value)
    parser.close()
    return parser.markdown()


def _bounded_get(url: str, *, timeout: int, max_bytes: int) -> tuple[bytes, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/markdown,text/plain,text/html,application/json;q=0.9,*/*;q=0.1",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content_type = resp.headers.get("content-type", "")
        body = resp.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"response too large; limit is {max_bytes} bytes")
    return body, content_type


def _assert_http_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be http:// or https://")


@skill(
    name="webfetch",
    display_name="Fetch Web Page",
    description=(
        "Fetch an HTTP/HTTPS URL and return text, markdown, or HTML. "
        "Use this for a specific URL. Use websearch when you need to discover pages. "
        "Large or non-text responses are rejected. Default format is markdown."
    ),
    category="system",
    params=[
        {"name": "url", "type": "string", "description": "HTTP or HTTPS URL to fetch."},
        {"name": "format", "type": "enum", "required": False,
         "options": ["markdown", "text", "html"],
         "description": "Return format. Default markdown."},
        {"name": "timeout", "type": "number", "required": False,
         "description": "Timeout in seconds, default 30, max 120."},
    ],
    returns="dict with keys: success, url, content_type, format, output, error",
)
def webfetch(url: str, format: str = "markdown", timeout: int = _DEFAULT_TIMEOUT) -> dict[str, Any]:
    try:
        _assert_http_url(url)
        fmt = format if format in {"markdown", "text", "html"} else "markdown"
        timeout = max(1, min(int(timeout or _DEFAULT_TIMEOUT), 120))
        body, content_type = _bounded_get(url, timeout=timeout, max_bytes=_MAX_FETCH_BYTES)
        mime = content_type.split(";", 1)[0].strip().lower()
        if mime and not (
            mime.startswith("text/")
            or mime in {"application/json", "application/xml", "application/xhtml+xml"}
            or mime.endswith("+json")
            or mime.endswith("+xml")
        ):
            return {"success": False, "url": url, "error": f"Unsupported content type: {content_type}"}
        raw = body.decode("utf-8", errors="replace")
        if fmt == "html":
            output = raw
        elif fmt == "text":
            output = _html_to_markdown(raw) if "html" in content_type else raw
        else:
            output = _html_to_markdown(raw) if "html" in content_type else raw
        return {
            "success": True,
            "url": url,
            "content_type": content_type,
            "format": fmt,
            "output": output,
            "error": None,
        }
    except Exception as e:
        logger.warning("[webfetch] failed for %s: %s", url, e)
        return {"success": False, "url": url, "error": str(e)}


def _parse_duckduckgo(html_text: str, limit: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(html_text):
        href = html.unescape(match.group("href"))
        parsed_href = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed_href.query)
        if "uddg" in qs:
            href = qs["uddg"][0]
        title = re.sub(r"<[^>]+>", "", match.group("title"))
        snippet = re.sub(r"<[^>]+>", "", match.group("snippet"))
        results.append({
            "title": html.unescape(title).strip(),
            "url": href,
            "snippet": html.unescape(snippet).strip(),
        })
        if len(results) >= limit:
            break
    return results


@skill(
    name="websearch",
    display_name="Search Web",
    description=(
        "Search the web for current information beyond model knowledge. "
        "Returns result titles, URLs, snippets, and a markdown summary. "
        "Use webfetch on a selected URL when detailed page content is needed."
    ),
    category="system",
    params=[
        {"name": "query", "type": "string", "description": "Search query."},
        {"name": "num_results", "type": "number", "required": False,
         "description": "Number of results, default 8, max 20."},
        {"name": "timeout", "type": "number", "required": False,
         "description": "Timeout in seconds, default 25, max 60."},
    ],
    returns="dict with keys: success, query, results, output, error",
)
def websearch(query: str, num_results: int = 8, timeout: int = 25) -> dict[str, Any]:
    try:
        limit = max(1, min(int(num_results or 8), 20))
        timeout = max(1, min(int(timeout or 25), 60))
        url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        body, _content_type = _bounded_get(url, timeout=timeout, max_bytes=_MAX_SEARCH_BYTES)
        html_text = body.decode("utf-8", errors="replace")
        results = _parse_duckduckgo(html_text, limit)
        output = "\n".join(
            f"{idx}. [{item['title']}]({item['url']})\n   {item['snippet']}"
            for idx, item in enumerate(results, 1)
        )
        if not results:
            output = "No search results found. Try a different query."
        return {
            "success": True,
            "query": query,
            "provider": "duckduckgo_html",
            "results": results,
            "output": output,
            "error": None,
            "raw_count": len(results),
        }
    except Exception as e:
        logger.warning("[websearch] failed for %s: %s", query, e)
        return {"success": False, "query": query, "results": [], "output": "", "error": str(e)}
