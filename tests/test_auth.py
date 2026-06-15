"""The Basic Auth gate protects public deploys — verify it allows good creds,
rejects bad/missing ones, and leaves the health check open for the platform."""
from __future__ import annotations

import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from momentum_desk.server import BasicAuthMiddleware


def _client():
    app = FastAPI()

    @app.get("/")
    def root():
        return {"page": "dashboard"}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    app.add_middleware(BasicAuthMiddleware, username="marc", password="s3cret")
    return TestClient(app)


def _auth(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_blocks_without_credentials():
    r = _client().get("/")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")


def test_allows_correct_credentials():
    assert _client().get("/", headers=_auth("marc", "s3cret")).status_code == 200


def test_rejects_wrong_password_and_user():
    c = _client()
    assert c.get("/", headers=_auth("marc", "nope")).status_code == 401
    assert c.get("/", headers=_auth("eve", "s3cret")).status_code == 401


def test_health_is_exempt():
    assert _client().get("/api/health").status_code == 200
