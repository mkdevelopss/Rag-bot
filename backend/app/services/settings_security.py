import hashlib
import hmac
import secrets
import time


PBKDF2_ITERATIONS = 600_000


def hash_settings_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


class SettingsAuth:
    def __init__(self, password_hash: str, token_ttl_seconds: int = 900):
        self.password_hash = password_hash
        self.token_ttl_seconds = token_ttl_seconds
        self._tokens: dict[str, float] = {}
        self._failed_attempts = 0
        self._locked_until = 0.0

    def _verify_password(self, password: str) -> bool:
        try:
            algorithm, iterations, salt_hex, digest_hex = self.password_hash.split("$")
            if algorithm != "pbkdf2_sha256":
                return False
            expected = bytes.fromhex(digest_hex)
            actual = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                bytes.fromhex(salt_hex),
                int(iterations),
            )
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(actual, expected)

    def unlock(self, password: str) -> tuple[str, int]:
        if not self.password_hash:
            raise RuntimeError("Settings password is not configured on the backend.")
        now = time.monotonic()
        if now < self._locked_until:
            raise PermissionError("Too many incorrect attempts. Wait one minute and try again.")
        if not self._verify_password(password):
            self._failed_attempts += 1
            if self._failed_attempts >= 5:
                self._failed_attempts = 0
                self._locked_until = now + 60
            raise PermissionError("Incorrect settings password.")
        self._failed_attempts = 0
        token = secrets.token_urlsafe(32)
        self._tokens[token] = now + self.token_ttl_seconds
        return token, self.token_ttl_seconds

    def is_authorized(self, token: str) -> bool:
        now = time.monotonic()
        expired = [key for key, expiry in self._tokens.items() if expiry <= now]
        for key in expired:
            del self._tokens[key]
        expiry = self._tokens.get(token)
        return expiry is not None and expiry > now

    def lock(self, token: str) -> None:
        self._tokens.pop(token, None)
