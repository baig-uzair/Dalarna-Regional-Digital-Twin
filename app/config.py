"""Runtime configuration, read from the environment (docker compose injects .env).

Values are read lazily through functions so tests can monkeypatch the environment.
No credentials have defaults here: a missing database password simply fails to connect.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if ENV_PATH.exists():
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass

PLACEHOLDERS = {"", "votre_cle_ici", "your_key_here", "changeme"}

_random_secret = secrets.token_urlsafe(32)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = _env(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def scheduler_workers() -> int:
    workers = _bounded_int("SCHEDULER_WORKERS", 4, 1, 32)
    if workers > db_pool_max_connections():
        raise ValueError("SCHEDULER_WORKERS cannot exceed POSTGRES_POOL_MAX")
    return workers


def db_pool_min_connections() -> int:
    return _bounded_int("POSTGRES_POOL_MIN", 1, 1, 32)


def db_pool_max_connections() -> int:
    minimum = db_pool_min_connections()
    return _bounded_int("POSTGRES_POOL_MAX", 12, minimum, 64)


def db_params() -> dict:
    params = {
        "host": _env("POSTGRES_HOST", "timescaledb"),
        "port": int(_env("POSTGRES_PORT", "5432")),
        "dbname": _env("POSTGRES_DB", "dalarna_dt"),
        "user": _env("POSTGRES_USER", "postgres"),
        "password": _env("POSTGRES_PASSWORD"),
        "connect_timeout": int(_env("POSTGRES_CONNECT_TIMEOUT", "1")),
    }
    sslmode = _env("POSTGRES_SSLMODE").strip()
    if sslmode:
        params["sslmode"] = sslmode
    return params


def admin_credentials() -> tuple[str, str]:
    return _env("ADMIN_USERNAME").strip(), _env("ADMIN_PASSWORD")


def session_secret() -> str:
    """SESSION_SECRET if set; otherwise a per-process random value (sessions reset on restart)."""
    return _env("SESSION_SECRET") or _random_secret


def session_secret_is_persistent() -> bool:
    return bool(_env("SESSION_SECRET"))


def public_simulation() -> bool:
    """Whether POST /api/simulation/run is open to Main Interface visitors (no admin login)."""
    return _env("PUBLIC_SIMULATION", "true").strip().lower() in ("1", "true", "yes", "on")


def scheduler_enabled() -> bool:
    return _env("SCHEDULER_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


def bootstrap_enabled() -> bool:
    """Schema apply + seed + scheduler start at app startup (tests switch this off)."""
    return _env("APP_BOOTSTRAP", "true").strip().lower() in ("1", "true", "yes", "on")


def orion_url() -> str:
    return _env("ORION_URL", "http://orion:1026/ngsi-ld/v1/entities")


def env_is_set(name: str) -> bool:
    """True when the variable holds a real value (never returns the value itself)."""
    return _env(name).strip().lower() not in PLACEHOLDERS
