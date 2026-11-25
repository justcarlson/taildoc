"""A small OpenID Connect provider for tests. It runs on loopback only."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

CLIENT_ID = "tailplan-test"
CLIENT_SECRET = "client-secret-for-tests"


class FakeIdP:
    """Issue ID tokens, keys, and userinfo for one client.

    Set `claims` for the next sign-in, and `userinfo` for the userinfo answer.
    Set `mode` to make discovery send an unsafe answer.
    """

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "key-1"
        self.claims: dict = {"sub": "user-1", "email": "user@example.test", "email_verified": True,
                             "name": "Test User", "groups": ["staff"]}
        self.userinfo: dict | None = None
        self.mode = "normal"
        self.algorithm = "RS256"
        self.codes: dict[str, dict] = {}
        self.requests: list[str] = []
        self.token_requests: list[dict] = []
        idp = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def send(self, status: int, body: object, headers: dict | None = None) -> None:
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                idp.requests.append(parsed.path)
                query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
                if parsed.path.endswith("/.well-known/openid-configuration"):
                    self.discovery()
                elif parsed.path == "/jwks":
                    self.send(200, {"keys": [idp.public_jwk()]})
                elif parsed.path == "/authorize":
                    code = secrets.token_urlsafe(16)
                    idp.codes[code] = query
                    location = query["redirect_uri"] + "?" + urlencode({"code": code, "state": query["state"]})
                    self.send_response(302)
                    self.send_header("Location", location)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif parsed.path == "/userinfo":
                    if self.headers.get("Authorization") != "Bearer access-" + idp.claims["sub"]:
                        self.send(401, {"error": "invalid_token"})
                    else:
                        self.send(200, {"sub": idp.claims["sub"], **(idp.userinfo or {})})
                elif parsed.path == "/slow":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    for _ in range(40):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.05)
                else:
                    self.send(404, {"error": "not_found"})

            def discovery(self) -> None:
                base = idp.issuer
                document = {
                    "issuer": idp.issuer, "authorization_endpoint": base + "/authorize",
                    "token_endpoint": base + "/token", "jwks_uri": base + "/jwks",
                    "userinfo_endpoint": base + "/userinfo",
                }
                if idp.mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", base + "/elsewhere")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if idp.mode == "cross-origin":
                    document["jwks_uri"] = "https://keys.example.test/jwks"
                if idp.mode == "wrong-issuer":
                    document["issuer"] = base + "/other"
                if idp.mode == "large":
                    document["padding"] = "x" * (1024 * 1024)
                self.send(200, document)

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                idp.requests.append(parsed.path)
                length = int(self.headers.get("Content-Length", "0"))
                form = {key: values[-1] for key, values in parse_qs(self.rfile.read(length).decode()).items()}
                if parsed.path != "/token":
                    self.send(404, {"error": "not_found"})
                    return
                expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
                idp.token_requests.append({"form": form, "authorization": self.headers.get("Authorization")})
                if self.headers.get("Authorization") != "Basic " + expected:
                    self.send(401, {"error": "invalid_client"})
                    return
                grant = idp.codes.pop(form.get("code", ""), None)
                if grant is None:
                    self.send(400, {"error": "invalid_grant"})
                    return
                challenge = base64.urlsafe_b64encode(
                    hashlib.sha256(form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
                if grant.get("code_challenge_method") != "S256" or challenge != grant.get("code_challenge"):
                    self.send(400, {"error": "invalid_grant", "error_description": "PKCE"})
                    return
                if form.get("redirect_uri") != grant.get("redirect_uri"):
                    self.send(400, {"error": "invalid_grant"})
                    return
                self.send(200, {"id_token": idp.id_token(nonce=grant["nonce"]),
                                "access_token": "access-" + idp.claims["sub"], "token_type": "Bearer"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.issuer = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def public_jwk(self) -> dict:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}

    def sign(self, claims: dict, *, algorithm: str | None = None, key: object = None,
             headers: dict | None = None) -> str:
        algorithm = algorithm or self.algorithm
        signing_key = key if key is not None else self.key
        return jwt.encode(claims, signing_key, algorithm=algorithm, headers={"kid": self.kid, **(headers or {})})

    def id_token(self, *, nonce: str, **overrides: object) -> str:
        now = int(time.time())
        claims = {"iss": self.issuer, "aud": CLIENT_ID, "iat": now, "exp": now + 300, "nonce": nonce,
                  **self.claims, **overrides}
        return self.sign({key: value for key, value in claims.items() if value is not None})

    def logout_token(self, **overrides: object) -> str:
        now = int(time.time())
        claims = {"iss": self.issuer, "aud": CLIENT_ID, "iat": now, "exp": now + 120, "jti": secrets.token_hex(8),
                  "sub": self.claims["sub"],
                  "events": {"http://schemas.openid.net/event/backchannel-logout": {}}, **overrides}
        return self.sign({key: value for key, value in claims.items() if value is not None})
