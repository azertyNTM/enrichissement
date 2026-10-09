import httpx
import pytest

from enrichissement import search


def test_retry_transient_then_ok(monkeypatch):
    monkeypatch.setattr(search.time, "sleep", lambda s: None)
    calls = iter([httpx.ReadTimeout("lent"), httpx.Response(503), httpx.Response(200, json={})])

    def send():
        x = next(calls)
        if isinstance(x, Exception):
            raise x
        x.request = httpx.Request("POST", "https://x")
        return x

    assert search.request_with_retry(send).status_code == 200


def test_no_retry_on_credits(monkeypatch):
    monkeypatch.setattr(search.time, "sleep", lambda s: None)
    n = []

    def send():
        n.append(1)
        return httpx.Response(400, text="Not enough credits", request=httpx.Request("POST", "https://x"))

    with pytest.raises(httpx.HTTPStatusError):
        search.request_with_retry(send)
    assert len(n) == 1
