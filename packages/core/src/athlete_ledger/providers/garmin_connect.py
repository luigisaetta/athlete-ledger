"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Wrap SDK authentication, safe retries and activity pagination.
"""  # noqa: D205, D212

from collections.abc import Callable, Iterator
from datetime import date, datetime
from functools import partial
from time import sleep as real_sleep
from typing import Any, Protocol, cast

import requests
from garmin_personal_api import (
    GarminPersonalApiClient,
    GarminPersonalApiError,
    HttpError,
    PersonalApiAuthenticator,
    __version__,
    resolve_api_key,
)
from pydantic import SecretStr

from athlete_ledger.db.models import utc_now
from athlete_ledger.normalize.garmin_connect import activity_id, records
from athlete_ledger.sync_types import ENDPOINTS, ProviderError, Response, UnexpectedResponseError


class Token(Protocol):
    """The only token attribute consumed by the adapter."""

    access_token: str


class Authenticator(Protocol):
    """Narrow authentication interface that can be replaced with a fake."""

    def obtain_token(self, api_key: str, *, force_refresh: bool = False) -> Token:
        """Obtain a cached or newly exchanged token.

        Args:
            api_key: Secret key, never logged.
            force_refresh: Ignore cached tokens when true.

        Returns:
            A bearer token.
        """
        ...

    def close(self) -> None:
        """Release the underlying authentication session."""
        ...


class Client(Protocol):
    """The shared lifecycle of SDK and fake clients."""

    def update_bearer_token(self, bearer_token: str) -> None:
        """Replace the bearer string without rendering it.

        Args:
            bearer_token: Secret access token.
        """
        ...

    def close(self) -> None:
        """Release the underlying data session."""
        ...


def _resolve_key(key: str | None) -> str:
    """Use the SDK's actual explicit-key argument, or its default discovery.

    Args:
        key: Optional explicit secret value.

    Returns:
        A resolved key string.
    """
    return resolve_api_key(api_key=key)


def _client_factory(token: str) -> Client:
    """Build an SDK client with a finite request timeout.

    Args:
        token: Secret bearer string.

    Returns:
        An unconnected data client.
    """
    return cast(Client, GarminPersonalApiClient(token, timeout=30))


class PersonalProvider:
    """Lazily authenticate and yield validated, unmodified raw responses."""

    sdk_version = __version__

    def __init__(
        self,
        key: SecretStr | None = None,
        *,
        client_factory: Callable[[str], Client] = _client_factory,
        authenticator_factory: Callable[[], Authenticator] | None = None,
        key_resolver: Callable[[str | None], str] = _resolve_key,
        sleep: Callable[[float], None] = real_sleep,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        """Configure factories without opening files or connections.

        Args:
            key: Optional configured API key.
            client_factory: Injectable data-client constructor.
            authenticator_factory: Injectable token-session constructor.
            key_resolver: Injectable secret discovery function.
            sleep: Backoff function, replaced in tests.
            clock: Aware clock for the SDK token cache.
        """
        self._configured_key = key
        self._key: SecretStr | None = None
        self._resolver = key_resolver
        self._client_factory = client_factory
        self._auth_factory = authenticator_factory
        self._sleep = sleep
        self._clock = clock
        self._client: Client | None = None
        self._auth: Authenticator | None = None
        self.request_count = 0

    def _token(self, force: bool = False) -> str:
        """Obtain a token through the retry boundary.

        Args:
            force: Whether to bypass token cache reuse.

        Returns:
            The secret access string.

        Raises:
            ProviderError: If token acquisition cannot complete.
        """
        if self._auth is None or self._key is None:
            raise ProviderError("POST token: authentication unavailable.")
        auth, key = self._auth, self._key.get_secret_value()
        token = cast(
            Token,
            self._attempt(
                "token",
                "POST",
                lambda: auth.obtain_token(key, force_refresh=force),
                refresh=False,
            ),
        )
        return token.access_token

    def _ensure_client(self) -> Client:
        """Initialize SDK objects only when a data request is needed.

        Returns:
            The authenticated client.

        Raises:
            ProviderError: If credential discovery or client construction fails.
        """
        if self._client is None:
            try:
                if self._key is None:
                    explicit = (
                        self._configured_key.get_secret_value()
                        if self._configured_key is not None
                        else None
                    )
                    self._key = SecretStr(self._resolver(explicit))
                if self._auth is None:
                    if self._auth_factory:
                        self._auth = self._auth_factory()
                    else:
                        auth = PersonalApiAuthenticator(
                            timeout=30,
                            clock=lambda: self._clock().timestamp(),
                        )
                        self._auth = cast(Authenticator, auth)
                        # The SDK creates new caches with 0600, but does not chmod existing files.
                        if auth.bearer_token_cache_file.exists():
                            auth.bearer_token_cache_file.chmod(0o600)
                self._client = self._client_factory(self._token())
            except (GarminPersonalApiError, requests.RequestException, OSError, ValueError):
                raise ProviderError("POST token: check personal API key validity/expiry.") from None
        return self._client

    def _attempt(
        self,
        endpoint: str,
        method: str,
        operation: Callable[[], object],
        *,
        refresh: bool = True,
    ) -> object:
        """Retry transient failures and refresh authentication at most once for a data 401.

        Args:
            endpoint: Application-controlled endpoint name used in sanitized failure
                messages.
            method: HTTP verb for this call; GET attempts increment the data-request
                counter.
            operation: Zero-argument SDK call to invoke again after a retry or token
                refresh.
            refresh: Allow one forced token refresh on a 401; false for token exchanges
                themselves.

        Returns:
            The SDK call's unchanged decoded JSON value, empty-body None, or token object
            for authentication calls.

        Raises:
            ProviderError: If authentication is rejected, a non-retryable response occurs,
                retries are exhausted, or the SDK call otherwise fails.
        """
        retries = 0
        refreshed = False
        while True:
            try:
                if method == "GET":
                    self.request_count += 1
                return operation()
            except HttpError as error:
                status = error.status_code
                if status == 401 and refresh and not refreshed:
                    refreshed = True
                    token = self._token(force=True)
                    if self._client is None:
                        raise ProviderError(
                            f"{method} {endpoint}: authentication unavailable."
                        ) from None
                    self._client.update_bearer_token(token)
                    continue
                if status in (401, 403):
                    raise ProviderError(
                        f"{method} {endpoint}: HTTP {status}; "
                        "check personal API key validity/expiry."
                    ) from None
                if status != 429 and not 500 <= status <= 599:
                    raise ProviderError(f"{method} {endpoint}: HTTP {status}.") from None
                if retries >= 3:
                    raise ProviderError(f"{method} {endpoint}: HTTP {status}.") from None
            except (requests.Timeout, requests.ConnectionError):
                if retries >= 3:
                    raise ProviderError(
                        f"{method} {endpoint}: transport retries exhausted."
                    ) from None
            except (GarminPersonalApiError, requests.RequestException, ValueError, OSError):
                raise ProviderError(f"{method} {endpoint}: request failed.") from None
            self._sleep(float(2 ** (retries + 1)))
            retries += 1

    def responses(self, dataset: str, start: date, end: date) -> Iterator[Response]:
        """Yield every page, including the terminating empty page.

        Args:
            dataset: Supported dataset name.
            start: Inclusive first calendar date.
            end: Inclusive last calendar date.

        Yields:
            Raw response pages with reproducible parameters.

        Raises:
            UnexpectedResponseError: If a page has invalid shape or makes no progress.
        """
        client = self._ensure_client()
        endpoint = ENDPOINTS[dataset]
        operation = cast(Callable[..., object], getattr(client, endpoint))
        offset = 0
        seen: set[int] = set()
        while True:
            params: dict[str, Any] = {"start_date": start.isoformat(), "end_date": end.isoformat()}
            if dataset == "activities":
                params.update(start=offset, limit=100)
            payload = self._attempt(endpoint, "GET", partial(operation, **params))
            page = records(dataset, payload)
            if dataset == "activities" and page:
                identifiers = {activity_id(item) for item in page}
                if not identifiers - seen:
                    raise UnexpectedResponseError(f"GET {endpoint}: pagination made no progress.")
                seen.update(identifiers)
            yield Response(endpoint, params, payload)
            if dataset != "activities" or not page:
                break
            offset += len(page)

    def close(self) -> None:
        """Close both sessions without exposing SDK errors.

        Raises:
            ProviderError: If a session cannot be closed.
        """
        failed = False
        for resource in (self._client, self._auth):
            if resource is not None:
                try:
                    resource.close()
                except (GarminPersonalApiError, requests.RequestException, OSError, ValueError):
                    failed = True
        if failed:
            raise ProviderError("GET token: session cleanup failed.")
