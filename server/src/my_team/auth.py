import hashlib
import hmac
import os
import secrets

from my_team.paths import private_data_dir

SIGNATURE_WINDOW_MS = 60_000
HEADER_TS, HEADER_NONCE, HEADER_SIG, HEADER_SESSION = (
    "x-my-team-ts", "x-my-team-nonce", "x-my-team-sig", "x-my-team-session")
_SCRYPT = {"n": 2**15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}


def load_secret() -> bytes:
    path = private_data_dir() / "secret"
    try:
        return path.read_bytes()
    except FileNotFoundError:
        value = secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
        return value


def signature(secret: bytes, method: str, path: str, ts: str, nonce: str, body: bytes) -> str:
    message = "\n".join((method.upper(), path, ts, nonce, hashlib.sha256(body).hexdigest())).encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def signed_headers(secret: bytes, method: str, path: str, body: bytes, now: int, session_id: str | None) -> dict:
    ts, nonce = str(now), secrets.token_hex(16)
    headers = {"X-My-Team": "1", HEADER_TS: ts, HEADER_NONCE: nonce,
               HEADER_SIG: signature(secret, method, path, ts, nonce, body)}
    if session_id:
        headers[HEADER_SESSION] = session_id
    return headers


class NonceCache:
    def __init__(self):
        self._seen: dict[str, int] = {}

    def accept(self, nonce: str, now: int) -> bool:
        if len(self._seen) > 10_000:
            self._seen = {n: t for n, t in self._seen.items() if now - t < 2 * SIGNATURE_WINDOW_MS}
        if nonce in self._seen:
            return False
        self._seen[nonce] = now
        return True


def verify_signature(secret: bytes, method: str, path: str, headers, body: bytes, now: int, nonces: NonceCache) -> bool:
    ts, nonce, sig = headers.get(HEADER_TS), headers.get(HEADER_NONCE), headers.get(HEADER_SIG)
    if not (ts and nonce and sig and ts.isdigit()) or abs(now - int(ts)) > SIGNATURE_WINDOW_MS:
        return False
    expected = signature(secret, method, path, ts, nonce, body)
    return hmac.compare_digest(expected, sig) and nonces.accept(nonce, now)


def health_proof(secret: bytes, nonce: str, port: int) -> str:
    return hmac.new(secret, f"health|{nonce}|{port}".encode(), hashlib.sha256).hexdigest()


def hash_passphrase(passphrase: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(passphrase.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_passphrase(passphrase: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        candidate = hashlib.scrypt(passphrase.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                                   maxmem=_SCRYPT["maxmem"], dklen=len(bytes.fromhex(digest)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest)


def new_session_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
