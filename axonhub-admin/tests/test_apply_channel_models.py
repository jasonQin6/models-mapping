"""Tests for the snapshot push script (apply_channel_models.py)."""

import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import apply_channel_models as apply  # noqa: E402


@pytest.fixture(autouse=True)
def _no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("AXONHUB_JWT", "AXONHUB_EMAIL", "AXONHUB_PASSWORD", "AXONHUB_URL"):
        monkeypatch.delenv(var, raising=False)


class FakeAxonHub:
    """Minimal channels store: reads serve state, the mutation updates it."""

    def __init__(self, channels: list[dict], *, verify_state: bool = True) -> None:
        self.channels = channels
        self.verify_state = verify_state
        self.mutations: list[dict] = []

    def fetch_connection(self, url: str, token: str, field: str, node_selection: str) -> list[dict]:
        assert field == "channels"
        return [dict(node) for node in self.channels]

    def fetch_graphql(self, url: str, token: str, query: str, variables: dict | None = None) -> dict:
        assert "updateChannel" in query
        variables = dict(variables or {})
        self.mutations.append(variables)
        if self.verify_state:
            for node in self.channels:
                if node["id"] == variables["id"]:
                    node["supportedModels"] = list(variables["input"]["supportedModels"])
                    node["autoSyncSupportedModels"] = variables["input"]["autoSyncSupportedModels"]
        return {"updateChannel": {}}


def _snapshot(tmp_path: Path, models: dict | None) -> Path:
    source = tmp_path / "snapshot.json"
    payload = {"commandcode-goat": {"id": "commandcode-goat", "models": models or {}}}
    source.write_text(json.dumps(payload), encoding="utf-8")
    return source


def _channel(models: list[str], *, auto_sync: bool = True, name: str = "commandcode-goat") -> dict:
    return {
        "id": "gid://axonhub/Channel/12",
        "name": name,
        "supportedModels": models,
        "autoSyncSupportedModels": auto_sync,
    }


def _install(monkeypatch: pytest.MonkeyPatch, hub: FakeAxonHub) -> None:
    monkeypatch.setattr(apply, "fetch_connection", hub.fetch_connection)
    monkeypatch.setattr(apply, "fetch_graphql", hub.fetch_graphql)


def _run(source: Path, *extra: str) -> int:
    return apply.main(
        ["--source", str(source), "--channel", "commandcode-goat", "--token", "t", *extra]
    )


def test_load_target_sorts_snapshot_keys(tmp_path: Path) -> None:
    source = _snapshot(tmp_path, {"b-model": {}, "a-model": {}})

    assert apply.load_target(source, "commandcode-goat") == ["a-model", "b-model"]


def test_load_target_refuses_missing_provider_or_empty_models(tmp_path: Path) -> None:
    source = _snapshot(tmp_path, None)

    with pytest.raises(apply.ApplyError, match="no non-empty models object"):
        apply.load_target(source, "commandcode-goat")
    with pytest.raises(apply.ApplyError, match="no non-empty models object"):
        apply.load_target(source, "other-provider")


def test_parse_expected_count() -> None:
    assert apply.parse_expected_count(None) is None
    assert apply.parse_expected_count("25:60") == (25, 60)
    for bad in ("25", "a:b", "60:25", "0:5"):
        with pytest.raises(apply.ApplyError):
            apply.parse_expected_count(bad)


def test_main_refuses_count_gate_before_any_network(tmp_path: Path) -> None:
    source = _snapshot(tmp_path, {"a": {}, "b": {}})

    assert _run(source, "--expected-count", "10:20") == 2


def test_main_refuses_missing_channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = FakeAxonHub([_channel(["a"], name="somewhere-else")])
    _install(monkeypatch, hub)
    source = _snapshot(tmp_path, {"a": {}})

    assert _run(source) == 2
    assert hub.mutations == []


def test_main_noop_when_in_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = FakeAxonHub([_channel(["a", "b"], auto_sync=False)])
    _install(monkeypatch, hub)
    source = _snapshot(tmp_path, {"b": {}, "a": {}})

    assert _run(source) == 0
    assert hub.mutations == []


def test_main_applies_drift_and_disables_autosync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = FakeAxonHub([_channel(["a", "stale"], auto_sync=True)])
    _install(monkeypatch, hub)
    source = _snapshot(tmp_path, {"a": {}, "b": {}})

    assert _run(source, "--expected-count", "1:5") == 0

    assert len(hub.mutations) == 1
    assert hub.mutations[0] == {
        "id": "gid://axonhub/Channel/12",
        "input": {"supportedModels": ["a", "b"], "autoSyncSupportedModels": False},
    }
    output = capsys.readouterr().out
    assert "updated and verified" in output
    assert "add: b" in output
    assert "remove: stale" in output


def test_main_dry_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = FakeAxonHub([_channel(["a"])] )
    _install(monkeypatch, hub)
    source = _snapshot(tmp_path, {"a": {}, "b": {}})

    assert _run(source, "--dry-run") == 0
    assert hub.mutations == []
    assert hub.channels[0]["supportedModels"] == ["a"]


def test_main_verification_mismatch_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = FakeAxonHub([_channel(["a"])], verify_state=False)
    _install(monkeypatch, hub)
    source = _snapshot(tmp_path, {"a": {}, "b": {}})

    assert _run(source) == 1
    assert len(hub.mutations) == 1


def test_main_requires_some_credential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _snapshot(tmp_path, {"a": {}})

    assert apply.main(["--source", str(source), "--channel", "commandcode-goat"]) == 2


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body.encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def test_sign_in_exchanges_credentials_for_token() -> None:
    requests: list[object] = []

    def opener(request: object, timeout: int) -> _FakeResponse:
        requests.append(request)
        return _FakeResponse('{"user": {"email": "ops@example.com"}, "token": "jwt-1"}')

    token = apply.sign_in("http://127.0.0.1:8868/", "ops@example.com", "secret", opener=opener)

    assert token == "jwt-1"
    assert len(requests) == 1
    assert requests[0].full_url == "http://127.0.0.1:8868/admin/auth/signin"
    assert json.loads(requests[0].data.decode("utf-8")) == {
        "email": "ops@example.com",
        "password": "secret",
    }


def test_sign_in_rejects_tokenless_response() -> None:
    def opener(request: object, timeout: int) -> _FakeResponse:
        return _FakeResponse('{"user": {"email": "ops@example.com"}}')

    with pytest.raises(RuntimeError, match="no token"):
        apply.sign_in("http://127.0.0.1:8868", "ops@example.com", "secret", opener=opener)
