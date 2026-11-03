"""ClientCredentialsTokenProvider against a local stub token endpoint."""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qsl

import pytest

from quantumbpm import ClientCredentialsTokenProvider
from quantumbpm.exceptions import ApiException


class TokenStub:
    """Answers every POST with ``status`` and the body ``respond(n)`` builds for request n."""

    def __init__(self, status, respond):
        self.forms: list[dict[str, str]] = []
        self.content_types: list[str | None] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
                stub.forms.append(dict(parse_qsl(raw)))
                stub.content_types.append(self.headers.get("Content-Type"))
                body = respond(len(stub.forms)).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/token"
        threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        ).start()

    def provider(self, **kwargs) -> ClientCredentialsTokenProvider:
        return ClientCredentialsTokenProvider(self.url, "my-client", "my-secret", **kwargs)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def make_stub():
    stubs: list[TokenStub] = []

    def make(status=200, respond=None, expires_in=',"expires_in":3600'):
        if respond is None:
            def respond(n):
                return f'{{"access_token":"token-{n}","token_type":"Bearer"{expires_in}}}'
        stub = TokenStub(status, respond)
        stubs.append(stub)
        return stub

    yield make
    for stub in stubs:
        stub.close()


def test_sends_grant_with_scopes_and_audience(make_stub):
    stub = make_stub()
    asyncio.run(stub.provider(scopes=["openid", "bpm.api"], audience="quantumbpm-api").get_token())

    assert stub.content_types == ["application/x-www-form-urlencoded"]
    assert stub.forms == [{
        "grant_type": "client_credentials",
        "client_id": "my-client",
        "client_secret": "my-secret",
        "scope": "openid bpm.api",
        "audience": "quantumbpm-api",
    }]


def test_omits_scope_and_audience_when_not_set(make_stub):
    stub = make_stub()
    asyncio.run(stub.provider(scopes=[]).get_token())

    assert stub.forms == [{
        "grant_type": "client_credentials",
        "client_id": "my-client",
        "client_secret": "my-secret",
    }]


@pytest.mark.parametrize(
    "expires_in",
    [',"expires_in":3600', ',"expires_in":"3600"', ""],
    ids=["number", "numeric string", "missing"],
)
def test_reuses_cached_token(make_stub, expires_in):
    stub = make_stub(expires_in=expires_in)
    provider = stub.provider()

    async def scenario():
        return [await provider.get_token(), await provider.get_token()]

    assert asyncio.run(scenario()) == ["token-1", "token-1"]
    assert len(stub.forms) == 1


def test_refreshes_token_near_expiry(make_stub):
    # 30 seconds is inside the 60 second refresh margin.
    stub = make_stub(expires_in=',"expires_in":30')
    provider = stub.provider()

    async def scenario():
        return [await provider.get_token(), await provider.get_token()]

    assert asyncio.run(scenario()) == ["token-1", "token-2"]
    assert len(stub.forms) == 2


def test_concurrent_callers_share_one_request(make_stub):
    stub = make_stub()
    provider = stub.provider()

    async def scenario():
        return await asyncio.gather(*(provider.get_token() for _ in range(5)))

    assert asyncio.run(scenario()) == ["token-1"] * 5
    assert len(stub.forms) == 1


def test_raises_api_exception_on_401(make_stub):
    stub = make_stub(status=401, respond=lambda _n: '{"error":"invalid_client"}')

    with pytest.raises(ApiException) as exc_info:
        asyncio.run(stub.provider().get_token())

    assert exc_info.value.status == 401
    assert "401" in str(exc_info.value)
    assert "invalid_client" in str(exc_info.value)


def test_requires_credentials():
    with pytest.raises(ValueError):
        ClientCredentialsTokenProvider("http://127.0.0.1:1/token", "my-client", "")
