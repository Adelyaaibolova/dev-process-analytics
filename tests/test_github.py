"""The HTTP client, tested with a fake opener (no network)."""

import io
import json
import urllib.error
from email.message import Message

import pytest

from devstats.github import GitHubClient, next_link, seconds_until_reset


class FakeResponse(io.BytesIO):
    def __init__(self, payload, headers=None):
        super().__init__(json.dumps(payload).encode())
        self.headers = headers or {}


def http_error(code, headers=None, body=b"{}"):
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError("https://api.github.com/x", code, "error", message, io.BytesIO(body))


class FakeOpener:
    """Replays a list of responses or exceptions and records requested URLs."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.urls = []

    def __call__(self, request, timeout=None):
        self.urls.append(request.full_url)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_next_link():
    header = (
        '<https://api.github.com/x?page=2>; rel="next", '
        '<https://api.github.com/x?page=5>; rel="last"'
    )
    assert next_link(header) == "https://api.github.com/x?page=2"
    assert next_link('<https://api.github.com/x?page=1>; rel="prev"') is None
    assert next_link(None) is None


def test_seconds_until_reset():
    assert seconds_until_reset({"retry-after": "30"}) == 30
    assert seconds_until_reset({"x-ratelimit-reset": "1100"}, now=1000) == 101
    assert seconds_until_reset({}) == 60


def test_paginate_follows_links():
    opener = FakeOpener(
        [
            FakeResponse([{"n": 1}, {"n": 2}], {"Link": '<https://api.github.com/x?page=2>; rel="next"'}),
            FakeResponse([{"n": 3}]),
        ]
    )
    client = GitHubClient("token", opener=opener)
    items = list(client.paginate("/x", {"state": "all"}))
    assert [item["n"] for item in items] == [1, 2, 3]
    assert opener.urls[0] == "https://api.github.com/x?state=all&per_page=100"
    assert opener.urls[1] == "https://api.github.com/x?page=2"


def test_paginate_with_item_key():
    opener = FakeOpener([FakeResponse({"total_count": 1, "workflow_runs": [{"id": 7}]})])
    client = GitHubClient(opener=opener)
    assert list(client.paginate("/runs", item_key="workflow_runs")) == [{"id": 7}]


def test_rate_limit_waits_and_retries():
    waits = []
    opener = FakeOpener(
        [
            http_error(403, {"X-RateLimit-Remaining": "0", "Retry-After": "5"}),
            FakeResponse({"ok": True}),
        ]
    )
    client = GitHubClient(opener=opener, sleep=waits.append)
    data, _ = client.get("/x")
    assert data == {"ok": True}
    assert waits == [5.0]


def test_server_error_is_retried():
    waits = []
    opener = FakeOpener([http_error(502), FakeResponse({"ok": True})])
    client = GitHubClient(opener=opener, sleep=waits.append)
    assert client.get("/x")[0] == {"ok": True}
    assert waits == [1]


def test_client_error_is_raised_with_body():
    opener = FakeOpener([http_error(404, body=b'{"message": "Not Found"}')])
    client = GitHubClient(opener=opener)
    with pytest.raises(RuntimeError, match="404.*Not Found"):
        client.get("/repos/nobody/nothing")


def test_token_goes_into_header():
    seen = {}

    def opener(request, timeout=None):
        seen.update(request.headers)
        return FakeResponse({})

    GitHubClient("secret", opener=opener).get("/x")
    assert seen["Authorization"] == "Bearer secret"
