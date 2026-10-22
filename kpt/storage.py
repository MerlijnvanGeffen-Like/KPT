"""Opslag voor gebruikers en tijdelijke sessies (geheugen lokaal, Redis/Upstash op Vercel)."""
from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time
import zlib
from pathlib import Path
from typing import Any

SESSION_TTL = int(os.environ.get("KPT_SESSION_TTL", str(2 * 60 * 60)))
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
MAX_SESSION_BYTES = 550 * 1024 * 1024
_B64 = "b64:"
# Upstash free/REST: houd encoded chunks onder ~750 KB.
_REST_CHUNK_CHARS = 700_000

_lock = threading.RLock()
_MEMORY_SESSIONS: dict[str, dict] = {}
_MEMORY_USERS: dict[str, dict] = {}
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_store = None
_store_failed = False


def is_vercel() -> bool:
    return bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))


def rest_credentials() -> tuple[str, str]:
    url = (
        os.environ.get("KV_REST_API_URL")
        or os.environ.get("UPSTASH_REDIS_REST_URL")
        or ""
    ).strip()
    token = (
        os.environ.get("KV_REST_API_TOKEN")
        or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
        or ""
    ).strip()
    return url, token


def tcp_redis_url() -> str:
    return (
        os.environ.get("REDIS_URL")
        or os.environ.get("KV_URL")
        or os.environ.get("UPSTASH_REDIS_URL")
        or ""
    ).strip()


def has_remote_store() -> bool:
    url, token = rest_credentials()
    return bool((url and token) or tcp_redis_url())


def require_redis_on_vercel() -> None:
    if is_vercel() and not has_remote_store():
        raise RuntimeError(
            "Op Vercel is Redis verplicht. Koppel Upstash Redis via Vercel → Storage/Integrations "
            "(zet KV_REST_API_URL + KV_REST_API_TOKEN) en redeploy."
        )


def _encode_value(value: bytes | str) -> str:
    if isinstance(value, bytes):
        packed = b"Z" + zlib.compress(value, 6)
        return _B64 + base64.b64encode(packed).decode("ascii")
    return value


def _decode_value(value: Any) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        if value.startswith(b"Z"):
            try:
                return zlib.decompress(value[1:])
            except zlib.error:
                return value
        return value
    text = str(value)
    if text.startswith(_B64):
        raw = base64.b64decode(text[len(_B64) :])
        if raw.startswith(b"Z"):
            try:
                return zlib.decompress(raw[1:])
            except zlib.error:
                return raw
        return raw
    return text.encode("utf-8")


class _UpstashStore:
    """HTTP Redis via Upstash REST (standaard na Vercel-integratie)."""

    def __init__(self, url: str, token: str):
        from upstash_redis import Redis

        self._redis = Redis(url=url, token=token)

    def _clear_chunks(self, key: str) -> None:
        marker = self._redis.get(f"{key}:chunks")
        to_delete = [key, f"{key}:chunks"]
        if marker is not None:
            try:
                count = int(marker)
            except (TypeError, ValueError):
                count = 0
            to_delete.extend(f"{key}:c:{index}" for index in range(count))
        self._redis.delete(*to_delete)

    def get_bytes(self, key: str) -> bytes | None:
        marker = self._redis.get(f"{key}:chunks")
        if marker is not None:
            try:
                count = int(marker)
            except (TypeError, ValueError):
                return None
            parts = []
            for index in range(count):
                part = self._redis.get(f"{key}:c:{index}")
                if part is None:
                    return None
                parts.append(str(part))
            return _decode_value("".join(parts))
        return _decode_value(self._redis.get(key))

    def set_bytes(self, key: str, value: bytes | str, ex: int | None = None) -> None:
        encoded = _encode_value(value) if isinstance(value, bytes) else value
        self._clear_chunks(key)
        if len(encoded) <= _REST_CHUNK_CHARS:
            if ex is None:
                self._redis.set(key, encoded)
            else:
                self._redis.set(key, encoded, ex=ex)
            return
        chunks = [encoded[i : i + _REST_CHUNK_CHARS] for i in range(0, len(encoded), _REST_CHUNK_CHARS)]
        if ex is None:
            self._redis.set(f"{key}:chunks", str(len(chunks)))
            for index, chunk in enumerate(chunks):
                self._redis.set(f"{key}:c:{index}", chunk)
        else:
            self._redis.set(f"{key}:chunks", str(len(chunks)), ex=ex)
            for index, chunk in enumerate(chunks):
                self._redis.set(f"{key}:c:{index}", chunk, ex=ex)

    def delete(self, *keys: str) -> None:
        if not keys:
            return
        for key in keys:
            self._clear_chunks(key)

    def llen(self, key: str) -> int:
        return int(self._redis.llen(key) or 0)

    def rpush(self, key: str, value: str) -> None:
        self._redis.rpush(key, value)

    def expire(self, key: str, seconds: int) -> None:
        self._redis.expire(key, seconds)

    def scan_keys(self, match: str) -> list[str]:
        cursor = 0
        found: list[str] = []
        while True:
            cursor, keys = self._redis.scan(cursor, match=match, count=100)
            found.extend(str(key) for key in keys)
            if int(cursor) == 0:
                break
        return found


class _TcpStore:
    """Klassieke Redis TCP-verbinding (REDIS_URL / KV_URL)."""

    def __init__(self, url: str):
        import redis

        self._redis = redis.from_url(url, decode_responses=False)
        self._redis.ping()

    def get_bytes(self, key: str) -> bytes | None:
        raw = self._redis.get(key)
        return raw if raw is None or isinstance(raw, bytes) else bytes(raw)

    def set_bytes(self, key: str, value: bytes | str, ex: int | None = None) -> None:
        data = value if isinstance(value, bytes) else value.encode("utf-8")
        self._redis.set(key, data, ex=ex)

    def delete(self, *keys: str) -> None:
        if keys:
            self._redis.delete(*keys)

    def llen(self, key: str) -> int:
        return int(self._redis.llen(key) or 0)

    def rpush(self, key: str, value: str) -> None:
        self._redis.rpush(key, value.encode("utf-8"))

    def expire(self, key: str, seconds: int) -> None:
        self._redis.expire(key, seconds)

    def scan_keys(self, match: str) -> list[str]:
        keys = []
        for key in self._redis.scan_iter(match=match):
            keys.append(key.decode("utf-8") if isinstance(key, bytes) else str(key))
        return keys


def get_store():
    global _store, _store_failed
    if _store is not None:
        return _store
    if _store_failed:
        return None
    try:
        rest_url, rest_token = rest_credentials()
        if rest_url and rest_token:
            _store = _UpstashStore(rest_url, rest_token)
            return _store
        tcp = tcp_redis_url()
        if tcp:
            _store = _TcpStore(tcp)
            return _store
        return None
    except Exception:
        _store_failed = True
        if is_vercel():
            raise
        return None


def local_users_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".kpt-tolverdeling"))
    return base / "KPT Tolverdeling" / "users.json"


def load_users() -> dict:
    store = get_store()
    if store is not None:
        raw = store.get_bytes("kpt:users")
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    if is_vercel():
        require_redis_on_vercel()

    path = local_users_path()
    if not path.exists():
        with _lock:
            return dict(_MEMORY_USERS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_users(users: dict) -> None:
    store = get_store()
    if store is not None:
        store.set_bytes("kpt:users", json.dumps(users, ensure_ascii=False).encode("utf-8"))
        return

    if is_vercel():
        require_redis_on_vercel()

    path = local_users_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    with _lock:
        _MEMORY_USERS.clear()
        _MEMORY_USERS.update(users)


def new_session_id() -> str:
    return secrets.token_urlsafe(24)


def empty_session(now: float | None = None) -> dict:
    return {
        "files": {},
        "state": {},
        "user": "",
        "admin": False,
        "updated": now or time.time(),
    }


def purge_expired_sessions(now: float | None = None) -> None:
    cutoff = (now or time.time()) - SESSION_TTL
    with _lock:
        for key, session in list(_MEMORY_SESSIONS.items()):
            if session.get("updated", 0) < cutoff:
                del _MEMORY_SESSIONS[key]


def load_session(session_id: str) -> dict | None:
    if not session_id:
        return None
    store = get_store()
    if store is not None:
        meta_raw = store.get_bytes(f"kpt:sess:{session_id}:meta")
        if not meta_raw:
            return None
        try:
            meta = json.loads(meta_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        files: dict[str, dict] = {}
        for name, info in (meta.get("files") or {}).items():
            data = store.get_bytes(f"kpt:sess:{session_id}:file:{name}")
            if data is None:
                continue
            files[name] = {
                "data": data,
                "mtime": float(info.get("mtime") or time.time()),
                "kind": info.get("kind") or "unknown",
            }
        return {
            "files": files,
            "state": meta.get("state") or {},
            "user": meta.get("user") or "",
            "admin": bool(meta.get("admin")),
            "updated": float(meta.get("updated") or time.time()),
        }

    with _lock:
        return _MEMORY_SESSIONS.get(session_id)


def save_session(session_id: str, session: dict) -> None:
    session["updated"] = time.time()
    store = get_store()
    if store is not None:
        previous_raw = store.get_bytes(f"kpt:sess:{session_id}:meta")
        previous_files = set()
        if previous_raw:
            try:
                previous_files = set((json.loads(previous_raw.decode("utf-8")).get("files") or {}).keys())
            except (UnicodeDecodeError, json.JSONDecodeError):
                previous_files = set()

        files = session.get("files") or {}
        meta = {
            "user": session.get("user") or "",
            "admin": bool(session.get("admin")),
            "state": session.get("state") or {},
            "updated": session["updated"],
            "files": {
                name: {"mtime": item.get("mtime"), "kind": item.get("kind"), "size": len(item.get("data") or b"")}
                for name, item in files.items()
            },
        }
        store.set_bytes(
            f"kpt:sess:{session_id}:meta",
            json.dumps(meta, ensure_ascii=False).encode("utf-8"),
            ex=SESSION_TTL,
        )
        keep = set(files)
        for name, item in files.items():
            store.set_bytes(f"kpt:sess:{session_id}:file:{name}", item.get("data") or b"", ex=SESSION_TTL)
        removed = previous_files - keep
        if removed:
            store.delete(*(f"kpt:sess:{session_id}:file:{name}" for name in removed))
        return

    with _lock:
        _MEMORY_SESSIONS[session_id] = session


def delete_session(session_id: str) -> None:
    if not session_id:
        return
    store = get_store()
    if store is not None:
        meta_raw = store.get_bytes(f"kpt:sess:{session_id}:meta")
        keys = [f"kpt:sess:{session_id}:meta"]
        if meta_raw:
            try:
                meta = json.loads(meta_raw.decode("utf-8"))
                keys.extend(f"kpt:sess:{session_id}:file:{name}" for name in (meta.get("files") or {}))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        store.delete(*keys)
        return
    with _lock:
        _MEMORY_SESSIONS.pop(session_id, None)


def clear_user_sessions(username: str) -> None:
    username = (username or "").strip().lower()
    if not username:
        return
    store = get_store()
    if store is not None:
        for key in store.scan_keys("kpt:sess:*:meta"):
            raw = store.get_bytes(key)
            if not raw:
                continue
            try:
                meta = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if (meta.get("user") or "").lower() == username:
                parts = key.split(":")
                if len(parts) >= 3:
                    delete_session(parts[2])
        return
    with _lock:
        for session in _MEMORY_SESSIONS.values():
            if (session.get("user") or "").lower() == username:
                session["files"].clear()
                session["state"] = {}
                session["user"] = ""
                session["admin"] = False


def login_is_blocked(key: str) -> bool:
    cutoff = time.time() - 10 * 60
    store = get_store()
    if store is not None:
        return store.llen(f"kpt:login:{key}") >= 8
    with _lock:
        attempts = [stamp for stamp in _LOGIN_ATTEMPTS.get(key, []) if stamp >= cutoff]
        _LOGIN_ATTEMPTS[key] = attempts
        return len(attempts) >= 8


def record_login_failure(key: str) -> None:
    store = get_store()
    if store is not None:
        redis_key = f"kpt:login:{key}"
        store.rpush(redis_key, str(time.time()))
        store.expire(redis_key, 10 * 60)
        return
    with _lock:
        _LOGIN_ATTEMPTS.setdefault(key, []).append(time.time())


def clear_login_failures(key: str) -> None:
    store = get_store()
    if store is not None:
        store.delete(f"kpt:login:{key}")
        return
    with _lock:
        _LOGIN_ATTEMPTS.pop(key, None)


def storage_mode() -> str:
    if rest_credentials()[0] and rest_credentials()[1]:
        return "upstash-rest"
    if tcp_redis_url():
        return "redis"
    if is_vercel():
        return "vercel-missing-redis"
    return "memory"
