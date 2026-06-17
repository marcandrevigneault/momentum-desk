"""Local HTTP -> HTTPS re-proxy between the IBKR CP Gateway and api.ibkr.com,
so the upstream TLS handshake comes from Python's ``ssl`` stack, not the JVM's.

Why this exists (carried over verbatim from bravos-interactive-link, where it
was diagnosed live): IBKR's edge WAF fingerprints the gateway's Java/Vert.x TLS
client (JA3/JA4) and can blanket-403 requests from an IP that has hit recent
rate limits — even after the IP is whitelisted. From the same machine and egress
IP, ``curl``/``httpx``/``urllib`` got 200 on ``/sso/Login`` while the gateway's
Vert.x client got 403 on the identical URL. Pointing the gateway's
``proxyRemoteHost`` at ``http://127.0.0.1:5002`` routes every upstream call
through this proxy; IBKR then sees Python's TLS fingerprint, which isn't blocked.

Run with ``python -m momentum_desk.ib_proxy``. Supervisord starts this before
ibeam so the gateway can reach its upstream from the first request. Port 5002
(not 5001) because ibeam binds its own health server on 5001.
"""
from __future__ import annotations

import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

UPSTREAM = "https://api.ibkr.com"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 5002
TIMEOUT = 30.0

# Hop-by-hop headers (RFC 7230 §6.1) — never forwarded; plus Content-Length,
# which we recompute.
HOP_BY_HOP = frozenset(
    h.lower()
    for h in (
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade",
    )
)

_log = logging.getLogger("ib_proxy")

# One httpx.Client shared across threads: pooling + HTTP/1.1 (the gateway expects
# HTTP/1.1 semantics; HTTP/2 reshapes the TLS hello and defeats the point).
_client: httpx.Client | None = None


def _client_get() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(http2=False, verify=True, timeout=TIMEOUT, follow_redirects=False)
    return _client


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "momentum-ib-proxy/0.1"

    def _forward(self, method: str) -> None:
        # The gateway's /v1/portal/* -> /v1/api/* rewrite middleware only runs
        # when proxyRemoteSsl=true; since we need that off, do the rewrite here.
        path = self.path
        if path.startswith("/v1/portal/"):
            path = "/v1/api/" + path[len("/v1/portal/") :]
        url = UPSTREAM + path

        headers: dict[str, str] = {}
        for k, v in self.headers.items():
            if k.lower() in HOP_BY_HOP:
                continue
            headers[k] = v
        headers["Host"] = "api.ibkr.com"
        # Force identity: httpx decodes gzip/br transparently but we forward the
        # body uncompressed, so any Content-Encoding header would be a lie.
        headers["Accept-Encoding"] = "identity"

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else None

        # Override the gateway's ssodh/init params. The working combo (verified
        # live in bravos on a paper-direct login) is publish+compete+force, which
        # acks the force-compete capability and takes over any prior session.
        if "ssodh/init" in path:
            base = path.split("?", 1)[0]
            path = f"{base}?publish=true&compete=true&force=true"
            url = UPSTREAM + path
            if body:
                if b'"compete":false' in body:
                    body = body.replace(b'"compete":false', b'"compete":true')
                if b"compete=false" in body:
                    body = body.replace(b"compete=false", b"compete=true")

        try:
            resp = _client_get().request(method, url, headers=headers, content=body)
        except httpx.HTTPError as exc:
            _log.warning("upstream error: %s %s: %s", method, self.path, exc)
            self.send_error(502, f"upstream error: {exc}")
            return

        _log.info(
            "%s %s%s -> %d (%d bytes)", method, self.path,
            f" (rewrote to {path})" if path != self.path else "",
            resp.status_code, len(resp.content),
        )

        self.send_response(resp.status_code)
        for k, v in resp.headers.items():
            lk = k.lower()
            if lk in HOP_BY_HOP or lk == "content-length":
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp.content)))
        self.end_headers()
        if resp.content:
            self.wfile.write(resp.content)

    def do_GET(self) -> None:
        self._forward("GET")

    def do_POST(self) -> None:
        self._forward("POST")

    def do_PUT(self) -> None:
        self._forward("PUT")

    def do_DELETE(self) -> None:
        self._forward("DELETE")

    def do_PATCH(self) -> None:
        self._forward("PATCH")

    def do_OPTIONS(self) -> None:
        self._forward("OPTIONS")

    def do_HEAD(self) -> None:
        self._forward("HEAD")

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # we log each request in _forward; silence the default stderr logger


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s ib_proxy: %(message)s",
    )
    srv = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Proxy)
    srv.daemon_threads = True
    srv.allow_reuse_address = True
    _log.info("listening on http://%s:%d -> %s (python TLS fingerprint)", LISTEN_HOST, LISTEN_PORT, UPSTREAM)
    try:
        srv.serve_forever()
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
