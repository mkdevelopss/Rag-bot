"""API keys for server-to-server use (MarketBid backend, DistributionOS backend, ...)."""

import fnmatch
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class ServiceKey:
    name: str
    key_hash: str
    namespace_glob: str

    def allows(self, namespace: str) -> bool:
        return fnmatch.fnmatchcase(namespace, self.namespace_glob)


def hash_service_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_service_key() -> str:
    return "ink_" + secrets.token_urlsafe(32)


def parse_service_keys(raw: str) -> list[ServiceKey]:
    keys = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 3 or len(parts[1]) != 64:
            raise ValueError(
                "SERVICE_API_KEYS entries must look like name:sha256hex:namespace_glob"
            )
        keys.append(ServiceKey(name=parts[0], key_hash=parts[1].lower(), namespace_glob=parts[2]))
    return keys


def require_service_key(request: Request) -> ServiceKey:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Missing service API key.")
    digest = hash_service_key(token)
    for key in request.app.state.service_keys:
        if hmac.compare_digest(digest, key.key_hash):
            return key
    raise HTTPException(status_code=401, detail="Invalid service API key.")
