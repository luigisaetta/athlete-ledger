"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Exercise provider behavior with fake clients and synthetic secrets.
"""  # noqa: D205, D212

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from athlete_ledger.providers.garmin_connect import PersonalProvider, _resolve_key
from athlete_ledger.sync_types import ProviderError, date_chunks
from garmin_personal_api import HttpError
from pydantic import SecretStr

START = date(2026, 1, 1)
MARKER = "SYNTHETIC-PRIVATE-RESPONSE-BODY"


class UnsafeHttpError(HttpError):
    """Simulate UnsafeHttpError."""

    def __str__(self):
        """Simulate str."""
        raise AssertionError("HttpError must never be rendered")


def http(status):
    """Simulate http.

    Args:
        status: Synthetic status input.

    Returns:
        The synthetic operation result.
    """
    return UnsafeHttpError(
        status_code=status,
        method="GET",
        url="https://synthetic.invalid/private",
        response_text=MARKER,
    )


class FakeClient:
    """Simulate FakeClient."""

    def __init__(self, outcomes):
        """Simulate init.

        Args:
            outcomes: Synthetic outcomes input.
        """
        self.outcomes = list(outcomes)
        self.calls = []
        self.tokens = []
        self.closed = False

    def fetch(self, **params):
        """Simulate fetch.

        Args:
            **params: Synthetic params input.

        Returns:
            The synthetic operation result.
        """
        self.calls.append(params)
        value = self.outcomes.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    search_activities = fetch
    get_daily_sleep_summaries = fetch
    get_daily_hrv_summaries = fetch
    get_daily_stress = fetch

    def update_bearer_token(self, token):
        """Simulate update bearer token.

        Args:
            token: Synthetic token input.
        """
        self.tokens.append(token)

    def close(self):
        """Simulate close."""
        self.closed = True


class FakeAuth:
    """Simulate FakeAuth."""

    def __init__(self, outcomes=None):
        """Simulate init.

        Args:
            outcomes: Synthetic outcomes input.
        """
        self.calls = []
        self.outcomes = list(outcomes or [])
        self.closed = False

    def obtain_token(self, key, *, force_refresh=False):
        """Simulate obtain token.

        Args:
            key: Synthetic key input.
            force_refresh: Synthetic force refresh input.

        Returns:
            The synthetic operation result.
        """
        self.calls.append(force_refresh)
        if self.outcomes:
            item = self.outcomes.pop(0)
            if isinstance(item, BaseException):
                raise item
        return SimpleNamespace(access_token="synthetic-access-token")

    def close(self):
        """Simulate close."""
        self.closed = True


def provider(outcomes, auth=None):
    """Simulate provider.

    Args:
        outcomes: Synthetic outcomes input.
        auth: Synthetic auth input.

    Returns:
        The synthetic operation result.
    """
    client = FakeClient(outcomes)
    auth = auth or FakeAuth()
    waits = []
    instance = PersonalProvider(
        SecretStr("synthetic-key"),
        client_factory=lambda token: client,
        authenticator_factory=lambda: auth,
        key_resolver=lambda explicit: explicit,
        sleep=waits.append,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    return instance, client, auth, waits


@pytest.mark.parametrize(("days", "expected"), [(1, 1), (7, 1), (8, 2), (29, 5)])
def test_inclusive_chunks(days, expected):
    """Verify inclusive chunks."""
    end = START + timedelta(days=days - 1)
    chunks = list(date_chunks(START, end))
    assert len(chunks) == expected
    assert chunks[0][0] == START and chunks[-1][1] == end
    assert sum((last - first).days + 1 for first, last in chunks) == days
    assert all(0 <= (last - first).days < 7 for first, last in chunks)


def test_key_resolver_uses_installed_sdk_argument(monkeypatch):
    """Verify key resolver uses installed sdk argument."""
    resolver = Mock(return_value="synthetic-resolved-key")
    monkeypatch.setattr("athlete_ledger.providers.garmin_connect.resolve_api_key", resolver)
    assert _resolve_key("synthetic-explicit") == "synthetic-resolved-key"
    resolver.assert_called_once_with(api_key="synthetic-explicit")
    resolver.reset_mock()
    _resolve_key(None)
    resolver.assert_called_once_with(api_key=None)


def test_lazy_factories_clock_and_secret_boundary(monkeypatch, tmp_path):
    """Verify lazy factories clock and secret boundary."""
    resolver, auth_factory, client_factory = Mock(), Mock(), Mock()
    instance = PersonalProvider(
        key_resolver=resolver, authenticator_factory=auth_factory, client_factory=client_factory
    )
    resolver.assert_not_called()
    auth_factory.assert_not_called()
    instance.close()
    resolver.assert_not_called()
    client, auth = FakeClient([[]]), FakeAuth()
    auth.bearer_token_cache_file = tmp_path / "synthetic-token-cache.json"
    auth.bearer_token_cache_file.write_text("synthetic-cache")
    auth.bearer_token_cache_file.chmod(0o644)
    build_auth = Mock(return_value=auth)
    monkeypatch.setattr(
        "athlete_ledger.providers.garmin_connect.PersonalApiAuthenticator", build_auth
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    instance = PersonalProvider(
        SecretStr("synthetic-key"),
        key_resolver=lambda key: key,
        client_factory=lambda token: client,
        clock=lambda: now,
    )
    list(instance.responses("sleep", START, START))
    assert build_auth.call_args.kwargs["clock"]() == now.timestamp()
    assert build_auth.call_args.kwargs["timeout"] == 30
    assert auth.bearer_token_cache_file.stat().st_mode & 0o777 == 0o600
    instance.close()
    assert client.closed and auth.closed
    assert "synthetic-key" not in repr(instance)


def test_401_refresh_once_then_success():
    """Verify 401 refresh once then success."""
    instance, client, auth, waits = provider([http(401), []])
    list(instance.responses("sleep", START, START))
    assert auth.calls == [False, True]
    assert client.tokens == ["synthetic-access-token"]
    assert instance.request_count == 2
    assert waits == []


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_fatal_after_refresh_or_forbidden(status):
    """Verify authentication fatal after refresh or forbidden."""
    outcomes = [http(401), http(401)] if status == 401 else [http(403)]
    instance, client, auth, waits = provider(outcomes)
    with pytest.raises(ProviderError) as error:
        list(instance.responses("sleep", START, START))
    assert "validity/expiry" in str(error.value)
    assert MARKER not in str(error.value)
    assert "synthetic.invalid" not in str(error.value)
    assert len(auth.calls) == (2 if status == 401 else 1)
    assert waits == []


@pytest.mark.parametrize("failure", [429, 500, 503, "timeout", "connection"])
def test_transient_retries_and_eventual_success(failure):
    """Verify transient retries and eventual success."""

    def make():
        """Return a fresh synthetic transient exception for each attempt."""
        if isinstance(failure, int):
            return http(failure)
        return (
            requests.Timeout(MARKER) if failure == "timeout" else requests.ConnectionError(MARKER)
        )

    instance, client, auth, waits = provider([make(), make(), make(), []])
    list(instance.responses("sleep", START, START))
    assert waits == [2, 4, 8]
    assert instance.request_count == 4


@pytest.mark.parametrize(
    "failure", [429, 500, requests.Timeout(MARKER), requests.ConnectionError(MARKER)]
)
def test_retry_exhaustion_never_renders_transport_values(failure):
    """Verify retry exhaustion never renders transport values."""
    error = http(failure) if isinstance(failure, int) else failure
    instance, client, auth, waits = provider([error] * 4)
    with pytest.raises(ProviderError) as caught:
        list(instance.responses("sleep", START, START))
    assert MARKER not in str(caught.value)
    assert waits == [2, 4, 8]
    assert len(client.calls) == 4


@pytest.mark.parametrize("status", [400, 404, 422])
def test_other_4xx_do_not_retry(status):
    """Verify other 4xx do not retry."""
    instance, client, auth, waits = provider([http(status)])
    with pytest.raises(ProviderError, match=f"HTTP {status}"):
        list(instance.responses("sleep", START, START))
    assert len(client.calls) == 1 and not waits


def test_auth_endpoint_uses_retry_boundary():
    """Verify auth endpoint uses retry boundary."""
    auth = FakeAuth([http(503), requests.Timeout(MARKER), None])
    instance, client, _, waits = provider([[]], auth)
    list(instance.responses("sleep", START, START))
    assert auth.calls == [False, False, False]
    assert waits == [2, 4]


def test_activity_paging_keeps_raw_pages_and_rejects_no_progress():
    """Verify activity paging keeps raw pages and rejects no progress."""
    pages = [[{"activityId": 1}, {"activityId": 2}], [{"activityId": 2}, {"activityId": 3}], []]
    instance, client, auth, waits = provider(pages)
    responses = list(instance.responses("activities", START, START))
    assert [item.payload for item in responses] == pages
    assert [item["start"] for item in client.calls] == [0, 100, 200]
    assert all(item["limit"] == 100 for item in client.calls)
    instance, *_ = provider([[{"activityId": 1}], [{"activityId": 1}]])
    with pytest.raises(ProviderError, match="no progress"):
        list(instance.responses("activities", START, START))


@pytest.mark.parametrize(
    ("dataset", "payload"),
    [
        ("sleep", {}),
        ("stress", "bad"),
        ("activities", {}),
        ("sleep", [1]),
        ("hrv", []),
        ("hrv", {}),
        ("hrv", {"hrvSummaries": None}),
    ],
)
def test_invalid_response_shapes(dataset, payload):
    """Verify invalid response shapes."""
    instance, *_ = provider([payload])
    with pytest.raises(ProviderError, match="response shape"):
        list(instance.responses(dataset, START, START))


@pytest.mark.parametrize("dataset", ["sleep", "hrv", "stress", "activities"])
def test_empty_body_is_an_empty_page(dataset):
    """Verify empty body is an empty page."""
    instance, *_ = provider([None])
    assert [r.payload for r in instance.responses(dataset, START, START)] == [None]


def test_cli_http_failure_never_formats_sdk_body(tmp_path, monkeypatch, capsys):
    """Verify real CLI and provider boundaries omit poisoned HTTP response bodies."""
    from athlete_ledger.cli import run
    from athlete_ledger.db import create_db_engine
    from athlete_ledger.db.migrate import upgrade
    from athlete_ledger.settings import Settings

    url = f"sqlite:///{tmp_path / 'synthetic-provider.db'}"
    engine = create_db_engine(Settings(db_url=url, _env_file=None))
    upgrade(engine)
    engine.dispose()
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", url)
    instance, client, auth, waits = provider([http(500)] * 4)
    monkeypatch.setattr("athlete_ledger.sync_cli.PersonalProvider", lambda key: instance)
    assert (
        run(
            ["sync", "backfill", "--since", str(START), "--until", str(START), "--dataset", "sleep"]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "HTTP 500" in captured.err
    assert MARKER not in captured.out + captured.err
    assert "synthetic-key" not in captured.out + captured.err
    assert "synthetic.invalid" not in captured.out + captured.err
    assert client.closed and auth.closed


def test_explicit_empty_key_is_not_treated_as_unset():
    """Verify an explicitly supplied empty secret does not trigger default key discovery."""
    from garmin_personal_api import GarminPersonalApiError

    resolver = Mock(side_effect=GarminPersonalApiError("SYNTHETIC-PRIVATE-ERROR"))
    instance = PersonalProvider(SecretStr(""), key_resolver=resolver)
    with pytest.raises(ProviderError) as error:
        list(instance.responses("sleep", START, START))
    resolver.assert_called_once_with("")
    assert "SYNTHETIC-PRIVATE" not in str(error.value)
