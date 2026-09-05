"""Unit tests for the shared GraphQL transport and fingerprint helpers."""

import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import common  # noqa: E402


def test_fetch_connection_joins_relay_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = iter(
        [
            {
                "channels": {
                    "edges": [{"node": {"id": "gid://axonhub/Channel/1", "name": "a"}}],
                    "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                }
            },
            {
                "channels": {
                    "edges": [{"node": {"id": "gid://axonhub/Channel/2", "name": "b"}}],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            },
        ]
    )
    calls: list[dict] = []

    def fake_fetch(url: str, token: str, query: str, variables: dict | None = None) -> dict:
        calls.append(dict(variables or {}))
        return next(pages)

    monkeypatch.setattr(common, "fetch_graphql", fake_fetch)

    nodes = common.fetch_connection("https://axonhub", "token", "channels", "id name")

    assert [node["name"] for node in nodes] == ["a", "b"]
    assert calls == [{"first": 100, "after": None}, {"first": 100, "after": "cursor-1"}]


def test_fetch_connection_rejects_repeated_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(url: str, token: str, query: str, variables: dict | None = None) -> dict:
        return {
            "channels": {
                "edges": [{"node": {"id": "gid://axonhub/Channel/1", "name": "a"}}],
                "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
            }
        }

    monkeypatch.setattr(common, "fetch_graphql", fake_fetch)

    with pytest.raises(RuntimeError, match="did not advance"):
        common.fetch_connection("https://axonhub", "token", "channels", "id name", page_size=10)


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body.encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def test_fetch_graphql_returns_data_object_only() -> None:
    def opener(request: object, timeout: int) -> _FakeResponse:
        return _FakeResponse('{"data": {"me": {"id": "u1"}}}')

    result = common.fetch_graphql(
        "https://axonhub", "token", "{ me { id } }", opener=opener
    )

    assert result == {"me": {"id": "u1"}}


def test_fetch_graphql_raises_on_graphql_errors() -> None:
    def opener(request: object, timeout: int) -> _FakeResponse:
        return _FakeResponse('{"errors": [{"message": "bad field"}]}')

    with pytest.raises(RuntimeError, match="returned an error"):
        common.fetch_graphql("https://axonhub", "token", "{ me { id } }", opener=opener)


def test_value_fingerprint_is_order_insensitive_and_stable() -> None:
    assert common.value_fingerprint({"a": 1, "b": [2, 3]}) == common.value_fingerprint(
        {"b": [2, 3], "a": 1}
    )
    assert common.value_fingerprint({"a": 1}) != common.value_fingerprint({"a": 2})
