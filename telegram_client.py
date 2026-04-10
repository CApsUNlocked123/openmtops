from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
import re
import os
import asyncio
import threading
from datetime import timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def _get_api_credentials() -> tuple[int, str]:
    """Read Telegram API credentials at call time so runtime config changes are picked up."""
    from runtime_config import get_telegram_credentials
    return get_telegram_credentials()


def _get_channel_id() -> int:
    """Return the monitored Telegram channel ID (configurable via Settings or .env)."""
    from runtime_config import get_telegram_channel_id
    return get_telegram_channel_id()
# Allow overriding session path via env var so Docker can mount it on a named volume.
SESSION_FILE = os.environ.get(
    "TELETHON_SESSION",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "anon"),
)

def is_tip(text):
    """Detect if a message is a trading tip."""
    if not text:
        return False
    text_lower = text.lower()
    return (
        text.strip().startswith('#') and
        any(kw in text_lower for kw in ['sl', 'target', 'pe', 'ce', 'call', 'put'])
    )

def parse_tip(text):
    """Parse key fields from a tip message."""
    symbol = re.search(r'#(\w+)', text, re.IGNORECASE)
    # Match plain "22900" OR concatenated "22900CE" / "22900PE"
    strike = re.search(r'\b(\d{4,6})(CE|PE)?\b', text, re.IGNORECASE)
    # Match standalone "CE"/"PE" or concatenated "22900CE"/"22900PE"
    option_type = re.search(r'(?<!\w)(PE|CE)(?!\w)|(?<=\d)(PE|CE)', text, re.IGNORECASE)
    # Match "above 90" or "@90" or "@ 90"
    entry = re.search(r'above\s+([\d.]+)|@\s*([\d.]+)', text, re.IGNORECASE)
    sl = re.search(r'SL\s*[:\-]?\s*([\d.]+)', text, re.IGNORECASE)
    targets = re.findall(r'[Tt]arget\s*[:\-]?\s*([\d./]+)', text)

    return {
        "symbol":   symbol.group(1).upper() if symbol else None,
        "strike":   strike.group(1) if strike else None,
        "type":     (option_type.group(1) or option_type.group(2)).upper() if option_type else None,
        "entry":    (entry.group(1) or entry.group(2)) if entry else None,
        "sl":       sl.group(1) if sl else None,
        "targets":  targets[0].split('/') if targets else [],
        "raw":      text.strip(),
    }

async def fetch_tips_list(limit=200):
    """Fetch tips using the shared persistent client (no event-loop churn)."""
    tips = []
    client = await _get_client()          # reuse the long-lived auth client
    async for msg in client.iter_messages(_get_channel_id(), limit=limit):
        if is_tip(msg.text):
            tip = parse_tip(msg.text)
            utc_date = msg.date.replace(tzinfo=timezone.utc) if msg.date.tzinfo is None else msg.date
            tip["date"] = utc_date.astimezone(IST).strftime("%d %b %Y %H:%M")
            tip["msg_id"] = msg.id
            tips.append(tip)
    return tips

def get_tips(limit=200):
    """Synchronous wrapper — runs on the persistent auth loop."""
    return _auth_run(fetch_tips_list(limit), timeout=60)

async def read_tips(limit=50):
    """Fetch recent tip messages from the channel."""
    api_id, api_hash = _get_api_credentials()
    async with TelegramClient(SESSION_FILE, api_id, api_hash) as client:
        print(f"Fetching last {limit} messages from channel {_get_channel_id()}...\n")
        async for msg in client.iter_messages(_get_channel_id(), limit=limit):
            if is_tip(msg.text):
                tip = parse_tip(msg.text)
                print(f"[{msg.date.strftime('%Y-%m-%d %H:%M')}]")
                print(f"  Symbol  : {tip['symbol']} {tip['strike']} {tip['type']}")
                print(f"  Entry   : {tip['entry']}")
                print(f"  SL      : {tip['sl']}")
                print(f"  Targets : {', '.join(tip['targets'])}")
                print(f"  Raw     : {tip['raw']}")
                print()

async def listen_live():
    """Listen for new tip messages in real time."""
    api_id, api_hash = _get_api_credentials()
    async with TelegramClient(SESSION_FILE, api_id, api_hash) as client:
        print("Listening for live tips... (Ctrl+C to stop)\n")

        async def _handler(event):
            if is_tip(event.raw_text):
                tip = parse_tip(event.raw_text)
                print(f"[NEW TIP] {event.date.strftime('%Y-%m-%d %H:%M')}")
                print(f"  Symbol  : {tip['symbol']} {tip['strike']} {tip['type']}")
                print(f"  Entry   : {tip['entry']}")
                print(f"  SL      : {tip['sl']}")
                print(f"  Targets : {', '.join(tip['targets'])}")
                print(f"  Raw     : {tip['raw']}")
                print()

        client.add_event_handler(_handler, events.NewMessage(chats=_get_channel_id()))

        await client.run_until_disconnected()

# ── Persistent background event loop for auth ─────────────────────────────────
# Telethon schedules internal tasks on the loop it was created on.
# Creating/closing a new loop per call corrupts those tasks and leaves the
# client in a "disconnected" state for the next call.  One long-lived loop
# running in a daemon thread avoids all of that.

_auth_loop:       asyncio.AbstractEventLoop | None = None
_auth_client:     TelegramClient | None = None
_auth_client_id:  int = 0   # api_id the current client was built with


def _get_auth_loop() -> asyncio.AbstractEventLoop:
    global _auth_loop
    if _auth_loop is None or _auth_loop.is_closed():
        _auth_loop = asyncio.new_event_loop()
        threading.Thread(target=_auth_loop.run_forever, daemon=True).start()
    return _auth_loop


def _auth_run(coro, timeout: int = 30):
    """Submit a coroutine to the persistent auth loop and block for the result."""
    return asyncio.run_coroutine_threadsafe(coro, _get_auth_loop()).result(timeout=timeout)


async def _get_client() -> TelegramClient:
    """Return the shared auth client, recreating if credentials have changed."""
    global _auth_client, _auth_client_id
    api_id, api_hash = _get_api_credentials()

    if not api_id or not api_hash:
        raise RuntimeError(
            "Telegram API credentials not configured. "
            "Set API ID and API Hash in Settings → Telegram API Credentials."
        )

    if _auth_client is None or _auth_client_id != api_id:
        # Disconnect old client first so it releases the session file lock
        if _auth_client is not None:
            try:
                await _auth_client.disconnect()
            except Exception:
                pass
        _auth_client    = TelegramClient(SESSION_FILE, api_id, api_hash)
        _auth_client_id = api_id

    if not _auth_client.is_connected():
        await _auth_client.connect()
    return _auth_client


def reset_telegram_client() -> None:
    """
    Force re-initialization of the Telegram client on next use.
    Call after saving new Telegram API credentials so the new
    api_id/api_hash are picked up without restarting the process.
    """
    global _auth_client, _auth_client_id
    if _auth_client is not None:
        try:
            asyncio.run_coroutine_threadsafe(
                _auth_client.disconnect(), _get_auth_loop()
            ).result(timeout=5)
        except Exception:
            pass
    _auth_client    = None
    _auth_client_id = 0


def is_authorized() -> bool:
    """Return True if the saved session is still valid."""
    async def _():
        client = await _get_client()
        return await client.is_user_authorized()
    try:
        return _auth_run(_())
    except Exception:
        return False


def send_code(phone: str) -> str:
    """Send the Telegram auth code to *phone*. Returns phone_code_hash."""
    async def _():
        client = await _get_client()
        result = await client.send_code_request(phone)
        return result.phone_code_hash
    return _auth_run(_())


def complete_sign_in(phone: str, code: str, phone_code_hash: str,
                     password: str | None = None) -> str:
    """
    Complete sign-in with the received code (and optional 2FA password).

    Returns:
        "ok"       – authorised successfully
        "2fa"      – 2-FA password required (call again with password=...)
        "error: …" – something went wrong
    """
    async def _():
        client = await _get_client()
        try:
            if password:
                await client.sign_in(password=password)
            else:
                await client.sign_in(phone, code,
                                   
                                     phone_code_hash=phone_code_hash)
            return "ok"
        except SessionPasswordNeededError:
            return "2fa"
        except Exception as exc:
            return f"error: {exc}"
    return _auth_run(_())


if __name__ == "__main__":
    import asyncio
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "history"

    if mode == "live":
        asyncio.run(listen_live())
    else:
        asyncio.run(read_tips(limit=50))