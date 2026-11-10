"""Minimal GitHub REST client: authentication, pagination and rate limits.

Only the standard library is used, so the extractor runs anywhere Python does.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterator

API_ROOT = "https://api.github.com"
_LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


def find_token() -> str | None:
    """Return a GitHub token from the environment or the GitHub CLI, if any."""
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = result.stdout.strip()
    return token if result.returncode == 0 and token else None


def next_link(link_header: str | None) -> str | None:
    """Extract the URL of the next page from a GitHub ``Link`` header."""
    if not link_header:
        return None
    match = _LINK_NEXT.search(link_header)
    return match.group(1) if match else None


def seconds_until_reset(headers: dict[str, str], now: float | None = None) -> float:
    """How long to wait after a rate-limit response (headers are lower-cased)."""
    retry_after = headers.get("retry-after")
    if retry_after:
        return float(retry_after)
    reset = headers.get("x-ratelimit-reset")
    if reset:
        current = time.time() if now is None else now
        return max(0.0, float(reset) - current) + 1.0
    return 60.0


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class GitHubClient:
    """Small wrapper around the GitHub REST API.

    ``opener`` and ``sleep`` can be replaced in tests.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        max_retries: int = 4,
        max_wait: float = 3600.0,
        opener: Callable = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.token = token
        self.max_retries = max_retries
        self.max_wait = max_wait
        self.opener = opener
        self.sleep = sleep
        self.requests_made = 0

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "dev-process-analytics",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get(self, url: str, params: dict | None = None) -> tuple[object, dict[str, str]]:
        """GET one URL and return ``(parsed JSON, lower-cased headers)``."""
        if not url.startswith("http"):
            url = API_ROOT + url
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)

        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(url, headers=self._headers())
            self.requests_made += 1
            try:
                with self.opener(request, timeout=60) as response:
                    headers = {k.lower(): v for k, v in response.headers.items()}
                    return json.load(response), headers
            except urllib.error.HTTPError as err:
                headers = {k.lower(): v for k, v in (err.headers or {}).items()}
                rate_limited = err.code in (403, 429) and (
                    headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers
                )
                if rate_limited and attempt < self.max_retries:
                    wait = seconds_until_reset(headers)
                    if wait > self.max_wait:
                        raise RuntimeError(
                            f"Rate limit reached; reset in {wait:.0f} s. "
                            "Set GITHUB_TOKEN for a higher limit."
                        ) from err
                    _log(f"Rate limit reached, waiting {wait:.0f} s ...")
                    self.sleep(wait)
                    continue
                if err.code >= 500 and attempt < self.max_retries:
                    self.sleep(2 ** attempt)
                    continue
                body = err.read().decode("utf-8", errors="replace")[:300]
                raise RuntimeError(f"GitHub API error {err.code} for {url}: {body}") from err
            except urllib.error.URLError:
                if attempt < self.max_retries:
                    self.sleep(2 ** attempt)
                    continue
                raise
        raise RuntimeError(f"Giving up on {url} after {self.max_retries} retries")

    def pages(self, url: str, params: dict | None = None) -> Iterator[object]:
        """Yield the parsed body of every page, following ``Link`` headers."""
        params = dict(params or {})
        params.setdefault("per_page", 100)
        data, headers = self.get(url, params)
        yield data
        following = next_link(headers.get("link"))
        while following:
            data, headers = self.get(following)
            yield data
            following = next_link(headers.get("link"))

    def paginate(
        self, url: str, params: dict | None = None, item_key: str | None = None
    ) -> Iterator[dict]:
        """Yield every item across pages (``item_key`` for wrapped lists)."""
        for page in self.pages(url, params):
            items = page[item_key] if item_key else page
            yield from items
