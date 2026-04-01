"""
Runtime credential overrides — stored in runtime_config.json next to this file.
Values here take precedence over globals.py so the settings page can update
Dhan credentials without modifying the original file.
"""

import json
import os

_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_config.json")


def _load() -> dict:
    try:
        with open(_CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def get_dhan_credentials() -> tuple[str, str]:
    """Return (client_id, access_token), preferring runtime_config over globals."""
    from globals import DHAN_CLIENTID, DHAN_ACCESSTOKEN
    cfg = _load()
    return (
        cfg.get("DHAN_CLIENTID")     or DHAN_CLIENTID,
        cfg.get("DHAN_ACCESSTOKEN")  or DHAN_ACCESSTOKEN,
    )


def save_dhan_credentials(client_id: str, access_token: str) -> None:
    cfg = _load()
    if client_id:
        cfg["DHAN_CLIENTID"] = client_id
    if access_token:
        cfg["DHAN_ACCESSTOKEN"] = access_token
    with open(_CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
