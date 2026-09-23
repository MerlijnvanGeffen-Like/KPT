"""Opslag voor gebruikers en tijdelijke sessies (geheugen lokaal, Redis op Vercel)."""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

SESSION_TTL = int(os.environ.get("KPT_SESSION_TTL", str(2 * 60 * 60)))
MAX_SESSION_BYTES = 100 * 1024 * 1024

_lock = threading.RLock()
_MEMORY_SESSIONS: dict[str, dict] = {}
_MEMORY_USERS: dict[str, dict] = {}
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_redis = None
_redis_failed = False


def is_vercel() -> bool:
    return bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))


def redis_url() -> str:
    return (
        os.environ.get("REDIS_URL")
        or os.environ.get("KV_URL")
        or os.environ.get("UPSTASH_REDIS_URL")
        or ""
    ).strip()


def require_redis_on_vercel() -> None:
    if is_vercel() and not redis_url():
        raise RuntimeError(
            "Op Vercel is REDIS_URL verplicht voor accounts en tijdelijke uploads. "
            "Koppel Upstash Redis in het Vercel-project."
        )


def get_redis():
    global _redis, _redis_failed
    url = redis_url()
    if not url:
        return None
    if _redis is not None:
        return _redis
    if _redis_failed:
        return None
    try:
        import redis

        client = redis.from_url(url, decode_responses=False)
        client.ping()
        _redis = client
        return client
    except Exception:
        _redis_failed = True
        if is_vercel():
            raise
        return None


def local_users_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".kpt-tolverdeling"))
    return base / "KPT Tolverdeling" / "users.json"


def load_users() -> dict:
    client = get_redis()
    if client is not None:
        raw = client.get("kpt:users")
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
    client = get_redis()
    if client is not None:
        client.set("kpt:users", json.dumps(users, ensure_ascii=False).encode("utf-8"))
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
    client = get_redis()
    if client is not None:
        meta_raw = client.get(f"kpt:sess:{session_id}:meta")
        if not meta_raw:
            return None
        try:
            meta = json.loads(meta_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        files: dict[str, dict] = {}
        for name, info in (meta.get("files") or {}).items():
            data = client.get(f"kpt:sess:{session_id}:file:{name}")
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
    client = get_redis()
    if client is not None:
        pipe = client.pipeline()
        meta = {
            "user": session.get("user") or "",
            "admin": bool(session.get("admin")),
            "state": session.get("state") or {},
            "updated": session["updated"],
            "files": {
                name: {"mtime": item.get("mtime"), "kind": item.get("kind"), "size": len(item.get("data") or b"")}
                for name, item in (session.get("files") or {}).items()
            },
        }
        pipe.set(f"kpt:sess:{session_id}:meta", json.dumps(meta, ensure_ascii=False).encode("utf-8"), ex=SESSION_TTL)
        existing = set()
        for key in client.scan_iter(match=f"kpt:sess:{session_id}:file:*"):
            existing.add(key.decode("utf-8") if isinstance(key, bytes) else key)
        keep = set()
        for name, item in (session.get("files") or {}).items():
            key = f"kpt:sess:{session_id}:file:{name}"
            keep.add(key)
            pipe.set(key, item.get("data") or b"", ex=SESSION_TTL)
        for key in existing - keep:
            pipe.delete(key)
        pipe.execute()
        return

    with _lock:
        _MEMORY_SESSIONS[session_id] = session


def delete_session(session_id: str) -> None:
    if not session_id:
        return
    client = get_redis()
    if client is not None:
        keys = [f"kpt:sess:{session_id}:meta"]
        keys.extend(client.scan_iter(match=f"kpt:sess:{session_id}:file:*"))
        if keys:
            client.delete(*keys)
        return
    with _lock:
        _MEMORY_SESSIONS.pop(session_id, None)


def clear_user_sessions(username: str) -> None:
    username = (username or "").strip().lower()
    if not username:
        return
    client = get_redis()
    if client is not None:
        for key in client.scan_iter(match="kpt:sess:*:meta"):
            raw = client.get(key)
            if not raw:
                continue
            try:
                meta = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            key_str = key.decode("utf-8") if isinstance(key, bytes) else key
            if (meta.get("user") or "").lower() == username:
                delete_session(key_str.split(":")[2])
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
    client = get_redis()
    if client is not None:
        return int(client.llen(f"kpt:login:{key}") or 0) >= 8
    with _lock:
        attempts = [stamp for stamp in _LOGIN_ATTEMPTS.get(key, []) if stamp >= cutoff]
        _LOGIN_ATTEMPTS[key] = attempts
        return len(attempts) >= 8


def record_login_failure(key: str) -> None:
    client = get_redis()
    if client is not None:
        redis_key = f"kpt:login:{key}"
        pipe = client.pipeline()
        pipe.rpush(redis_key, str(time.time()).encode("utf-8"))
        pipe.expire(redis_key, 10 * 60)
        pipe.execute()
        return
    with _lock:
        _LOGIN_ATTEMPTS.setdefault(key, []).append(time.time())


def clear_login_failures(key: str) -> None:
    client = get_redis()
    if client is not None:
        client.delete(f"kpt:login:{key}")
        return
    with _lock:
        _LOGIN_ATTEMPTS.pop(key, None)


def storage_mode() -> str:
    if redis_url():
        return "redis"
    if is_vercel():
        return "vercel-missing-redis"
    return "memory"
