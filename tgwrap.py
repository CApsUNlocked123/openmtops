from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from globals import API_APP, API_HASH
import re
import os
import asyncio
import threading
from datetime import timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

api_id = int(API_APP)
api_hash = API_HASH

CHANNEL_ID = -1001881641339
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
    strike = re.search(r'\b(\d{4,6})\b', text)
    option_type = re.search(r'\b(PE|CE)\b', text, re.IGNORECASE)
    entry = re.search(r'above\s+([\d.]+)', text, re.IGNORECASE)
    sl = re.search(r'SL\s+([\d.]+)', text, re.IGNORECASE)
    targets = re.findall(r'[Tt]arget\s+([\d./]+)', text)

    return {
        "symbol":   symbol.group(1).upper() if symbol else None,
        "strike":   strike.group(1) if strike else None,
        "type":     option_type.group(1).upper() if option_type else None,
        "entry":    entry.group(1) if entry else None,
        "sl":       sl.group(1) if sl else None,
        "targets":  targets[0].split('/') if targets else [],
        "raw":      text.strip(),
    }

async def fetch_tips_list(limit=50):
    """Fetch tips using the shared persistent client (no event-loop churn)."""
    tips = []
    client = await _get_client()          # reuse the long-lived auth client
    async for msg in client.iter_messages(CHANNEL_ID, limit=limit):
        if is_tip(msg.text):
            tip = parse_tip(msg.text)
            utc_date = msg.date.replace(tzinfo=timezone.utc) if msg.date.tzinfo is None else msg.date
            tip["date"] = utc_date.astimezone(IST).strftime("%d %b %Y %H:%M")
            tip["msg_id"] = msg.id
            tips.append(tip)
    return tips

def get_tips(limit=50):
    """Synchronous wrapper for Streamlit — runs on the persistent auth loop."""
    return _auth_run(fetch_tips_list(limit), timeout=60)

async def read_tips(limit=50):
    """Fetch recent tip messages from the channel."""
    async with TelegramClient(SESSION_FILE, api_id, api_hash) as client:
        print(f"Fetching last {limit} messages from channel {CHANNEL_ID}...\n")
        async for msg in client.iter_messages(CHANNEL_ID, limit=limit):
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

        client.add_event_handler(_handler, events.NewMessage(chats=CHANNEL_ID))

        await client.run_until_disconnected()

# ── Persistent background event loop for auth ─────────────────────────────────
# Telethon schedules internal tasks on the loop it was created on.
# Creating/closing a new loop per call corrupts those tasks and leaves the
# client in a "disconnected" state for the next call.  One long-lived loop
# running in a daemon thread avoids all of that.

_auth_loop:   asyncio.AbstractEventLoop | None = None
_auth_client: TelegramClient | None = None


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
    """Return the shared auth client, (re)connecting as needed."""
    global _auth_client
    if _auth_client is None:
        _auth_client = TelegramClient(SESSION_FILE, api_id, api_hash)
    if not _auth_client.is_connected():
        await _auth_client.connect()
    return _auth_client


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