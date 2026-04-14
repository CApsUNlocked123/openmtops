"""
Unified configuration accessor for OpenMTOps.

Priority order (highest to lowest):
  1. config.json  — written by the setup wizard and Settings page
  2. os.environ   — populated by load_dotenv() from .env on startup
  3. empty string / safe defaults

Public API (stable signatures — callers won't break):
  get(key, default)              — dotted key, e.g. "telegram.api_id"
  set_many(updates)              — batch write to config.json
  get_dhan_credentials()         — (client_id, access_token)
  save_dhan_credentials(id, tok) — persist + flush .env
  get_telegram_credentials()     — (api_id: int, api_hash: str)
  get_telegram_channel_id()      — int
  get_secret_key()               — auto-generates if absent
  is_configured()                — True if minimum required fields present
  flush_to_dotenv()              — write config.json → .env for restart persistence
"""

import json
import os
import secrets

_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
_DOTENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

# ── Internal helpers ──────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        with open(_CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict) -> None:
    with open(_CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _set_dotted(data: dict, key: str, value) -> dict:
    """Write a dotted key (e.g. 'telegram.api_id') into a nested dict."""
    parts = key.split(".", 1)
    if len(parts) == 1:
        data[key] = value
    else:
        sub = data.setdefault(parts[0], {})
        _set_dotted(sub, parts[1], value)
    return data


def _get_dotted(data: dict, key: str, default=None):
    """Read a dotted key (e.g. 'telegram.api_id') from a nested dict."""
    parts = key.split(".", 1)
    if len(parts) == 1:
        return data.get(key, default)
    return _get_dotted(data.get(parts[0], {}), parts[1], default)


# ── Migration from old runtime_config.json ───────────────────────────────────

def _migrate_legacy() -> None:
    """Migrate runtime_config.json → config.json on first run after upgrade."""
    legacy_path = os.path.join(os.path.dirname(_CONFIG_FILE), "runtime_config.json")
    if os.path.exists(legacy_path) and not os.path.exists(_CONFIG_FILE):
        try:
            with open(legacy_path) as f:
                legacy = json.load(f)
            data = {}
            dhan = data.setdefault("dhan", {})
            if legacy.get("DHAN_CLIENTID"):
                dhan["client_id"] = legacy["DHAN_CLIENTID"]
            if legacy.get("DHAN_ACCESSTOKEN"):
                dhan["access_token"] = legacy["DHAN_ACCESSTOKEN"]
            _save(data)
        except Exception:
            pass


_migrate_legacy()


# ── Public API ────────────────────────────────────────────────────────────────

def get(key: str, default=None):
    """
    Read a config value by dotted key.
    Checks config.json first, then os.environ (uppercased leaf key).
    """
    cfg = _load()
    val = _get_dotted(cfg, key)
    if val:
        return val
    # Fall back to environment — use the leaf key uppercased
    env_key = key.split(".")[-1].upper()
    return os.environ.get(env_key, default)


def set_many(updates: dict) -> None:
    """
    Batch write dotted keys to config.json.
    Does NOT auto-flush .env — call flush_to_dotenv() explicitly when needed.

    Example:
        set_many({"telegram.api_id": 12345, "app_pin": "1234"})
    """
    data = _load()
    for key, value in updates.items():
        _set_dotted(data, key, value)
    _save(data)


# ── Credential accessors ──────────────────────────────────────────────────────

def get_dhan_credentials() -> tuple[str, str]:
    """Return (client_id, access_token), preferring config.json over os.environ."""
    cfg = _load()
    dhan = cfg.get("dhan", {})
    client_id = dhan.get("client_id") or os.environ.get("DHAN_CLIENTID", "")
    token     = dhan.get("access_token") or os.environ.get("DHAN_ACCESSTOKEN", "")
    return client_id, token


def save_dhan_credentials(client_id: str, access_token: str) -> None:
    """Persist Dhan credentials to config.json and flush .env."""
    data = _load()
    dhan = data.setdefault("dhan", {})
    if client_id:
        dhan["client_id"] = client_id
    if access_token:
        dhan["access_token"] = access_token
    _save(data)
    flush_to_dotenv()


def get_telegram_credentials() -> tuple[int, str]:
    """Return (api_id: int, api_hash: str)."""
    cfg = _load()
    tg = cfg.get("telegram", {})
    api_id   = tg.get("api_id")   or int(os.environ.get("TELEGRAM_API_APP",  0) or 0)
    api_hash = tg.get("api_hash") or os.environ.get("TELEGRAM_API_HASH", "")
    return int(api_id) if api_id else 0, api_hash


def get_telegram_channel_id() -> int:
    """Return the Telegram channel ID to monitor for tips."""
    cfg = _load()
    val = cfg.get("telegram_channel_id") or os.environ.get("TELEGRAM_CHANNEL_ID")
    if val:
        return int(val)
    return -1001881641339  # default channel (overridable via wizard / Settings)


def get_secret_key() -> str:
    """
    Return the Flask SECRET_KEY.
    If absent in config.json and os.environ, auto-generates one and saves it
    so it remains stable across restarts (sessions survive server restart).
    """
    cfg = _load()
    key = cfg.get("secret_key") or os.environ.get("SECRET_KEY", "")
    if not key:
        key = secrets.token_hex(32)
        cfg["secret_key"] = key
        _save(cfg)
    return key


def is_configured() -> bool:
    """
    Return True if the minimum required credentials are present in either
    config.json or os.environ.

    Minimum: Dhan client_id + access_token, AND either
             (Telegram api_id + api_hash) OR (telegram.skipped == true).
    """
    cfg = _load()
    tg   = cfg.get("telegram", {})
    dhan = cfg.get("dhan", {})

    tg_id   = tg.get("api_id")   or int(os.environ.get("TELEGRAM_API_APP",  0) or 0)
    tg_hash = tg.get("api_hash") or os.environ.get("TELEGRAM_API_HASH", "")
    d_id    = dhan.get("client_id")    or os.environ.get("DHAN_CLIENTID",    "")
    d_tok   = dhan.get("access_token") or os.environ.get("DHAN_ACCESSTOKEN", "")
    tg_skip = tg.get("skipped", False)

    return bool((tg_skip or (tg_id and tg_hash)) and d_id and d_tok)


# ── .env flush ────────────────────────────────────────────────────────────────

def flush_to_dotenv() -> None:
    """
    Write all credentials from config.json into .env so they survive restarts.

    Called:
      - By the setup wizard on /setup/complete (primary path)
      - By every Settings page update route (keeps .env in sync)

    .env is gitignored. Power users who hand-edit .env and restart will have
    their values read by load_dotenv() → os.environ → is_configured() returns
    True → wizard is skipped.
    """
    cfg  = _load()
    tg   = cfg.get("telegram", {})
    dhan = cfg.get("dhan", {})

    lines = [
        f"SECRET_KEY={cfg.get('secret_key', '')}",
        f"APP_PIN={cfg.get('app_pin', '')}",
        f"TELEGRAM_API_APP={tg.get('api_id', '')}",
        f"TELEGRAM_API_HASH={tg.get('api_hash', '')}",
        f"TELEGRAM_CHANNEL_ID={cfg.get('telegram_channel_id', '')}",
        f"DHAN_CLIENTID={dhan.get('client_id', '')}",
        f"DHAN_ACCESSTOKEN={dhan.get('access_token', '')}",
        f"DHAN_APIKEY={dhan.get('api_key', '')}",
        f"DHAN_APISECRET={dhan.get('api_secret', '')}",
        "TESTING=0",
    ]
    with open(_DOTENV_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")
