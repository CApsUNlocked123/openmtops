# MarketMascot — F&O Options Trading Assistant

> **AI-agent context file.** This README is the single source of truth for any AI agent or developer modifying this codebase. Read it fully before touching any file.

---

## What This App Does

MarketMascot is a **single-user Flask + SocketIO web app** for Indian F&O (Futures & Options) trading. It:

1. Reads trading signals from a private **Telegram channel** using Telethon
2. Shows parsed tip cards (symbol, strike, CE/PE, entry, SL, targets)
3. Lets the user click-to-execute into **Dhan** brokerage via dhanhq SDK
4. Watches price live over **DhanHQ WebSocket** and auto-exits at SL/targets
5. Provides an **Option Analyzer** (option chain, OI, Greeks, live LTP)
6. Provides an **OI Tracker** (records OI buildup per strike over time)
7. Provides a **Strategy Dashboard** (regime, phase, EMA, OI walls — powered by stored 5-min candles)
8. Sends **in-app notifications** (bell dropdown, real-time via SocketIO) for new tips and ENTER signals

---

## Quick-Start (Local Dev)

```bash
cd /path/to/main
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # fill in credentials (see .env section)
python app.py                     # runs on http://127.0.0.1:5000
```

First run: navigate to `/auth/status` — if Dhan token and Telegram session are valid, you enter the app. If Telegram is not authorized, go to `/settings` to complete the phone → code → (optional 2FA) wizard.

---

## Hosting as SaaS (Production)

### Prerequisites
- Linux VPS (Ubuntu 22.04+)
- Python 3.12
- Nginx
- Systemd

### Step-by-step

```bash
# 1. Clone repo on server
git clone <repo> /opt/marketmascot
cd /opt/marketmascot

# 2. Create virtualenv
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Create .env with all secrets
cp .env.example .env
nano .env   # fill SECRET_KEY, APP_PIN, DHAN_*, TELEGRAM_*

# 4. Generate SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"

# 5. Install systemd service
sudo cp deployment/marketmascot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable marketmascot
sudo systemctl start marketmascot

# 6. Install nginx reverse proxy
sudo cp deployment/nginx.conf /etc/nginx/sites-available/marketmascot
sudo ln -s /etc/nginx/sites-available/marketmascot /etc/nginx/sites-enabled/
sudo nginx -t && sudo nginx -s reload
```

### Security for multi-user SaaS
- Set `APP_PIN` in `.env` — every visitor must enter PIN before seeing the app
- The PIN gate is in `routes/auth.py`: rate-limited to 5 attempts/hour/IP
- The `/auth/status` page checks both Dhan token (JWT exp) and Telegram session
- All credential updates happen via `/settings` without touching `.env`

---

## Environment Variables (`.env`)

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Flask session signing key — generate with `secrets.token_hex(32)` |
| `APP_PIN` | Optional | 4–8 digit PIN gate. Leave blank to disable. |
| `DHAN_CLIENTID` | Yes | Dhan Client ID |
| `DHAN_ACCESSTOKEN` | Yes | Dhan JWT access token (expires annually) |
| `DHAN_APIKEY` | Optional | Dhan API key (future use) |
| `DHAN_APISECRET` | Optional | Dhan API secret (future use) |
| `TELEGRAM_API_APP` | Yes | Telegram API App ID (from my.telegram.org) |
| `TELEGRAM_API_HASH` | Yes | Telegram API Hash (from my.telegram.org) |
| `TELETHON_SESSION` | Optional | Override path for `anon.session` file (Docker mount) |

**Credential override chain:** `runtime_config.json` > `.env` > hardcoded defaults.
The Settings page writes to `runtime_config.json` — changes take effect immediately without restart.

---

## Folder Structure

```
main/
├── app.py                    # Entry point — Flask factory, blueprints, auth guard
├── globals.py                # Loads .env via python-dotenv; exports all env vars
├── runtime_config.py         # JSON-backed credential override (Settings page writes here)
├── extensions.py             # Shared SocketIO instance (avoids circular imports)
├── dhan.py                   # Dhan SDK init + instrument master lookup
├── tgwrap.py                 # Telethon wrapper — auth wizard, get_tips()
├── price_feed.py             # DhanHQ WebSocket wrapper — start/stop/get_tick
├── feed_manager.py           # Named-subscriber registry sharing one WebSocket
├── candle_service.py         # Background daemon: 5-min candles → SQLite
├── indicators.py             # OI analysis: max pain, PCR, wall classify, signals
├── indicators_dashboard.py   # Strategy dashboard: regime, phase, EMA, linear score
├── notification_service.py   # In-app notification bus + Telegram tips poller
├── signal_notifier.py        # Market-hours ENTER signal scanner (30-min cooldown)
├── requirements.txt
├── .env.example              # Template for .env — copy and fill
├── .gitignore
│
├── routes/
│   ├── __init__.py
│   ├── auth.py               # /pin and /auth/status — PIN gate + credential health
│   ├── home.py               # / — home page with Dhan position count
│   ├── tips.py               # /tips — Telegram tips browse + execute
│   ├── custom.py             # /custom — manual trade entry
│   ├── live.py               # /live — SocketIO trade state machine
│   ├── analyzer.py           # /analyzer — option chain + live OI stream
│   ├── oi_tracker.py         # /oi-tracker — per-strike OI recording
│   ├── dashboard.py          # /dashboard — strategy dashboard + API endpoints
│   ├── history.py            # /history — completed trades + P&L
│   ├── settings.py           # /settings — Telegram auth wizard + Dhan token form
│   └── notifications.py      # /api/notifications/* — bell API
│
├── templates/
│   ├── base.html             # Shared layout: navbar, bell dropdown, SocketIO JS
│   ├── home.html             # Feature card grid
│   ├── tips.html             # Tips table + selected tip detail + execute form
│   ├── custom.html           # Manual trade form
│   ├── live.html             # Live price + state banner + P&L tracker
│   ├── analyzer.html         # Option chain table + symbol selector
│   ├── oi_tracker.html       # OI tracking table + start/stop controls
│   ├── dashboard.html        # Strategy dashboard panels (polls API every 5s)
│   ├── history.html          # Trade log table + P&L summary
│   ├── settings.html         # Telegram wizard steps + Dhan token form
│   ├── auth_status.html      # Standalone credential health page
│   ├── pin.html              # Standalone PIN entry page
│   └── restarting.html       # Standalone "app restarting" countdown page
│
├── static/
│   ├── css/
│   │   └── style.css         # Full glassmorphism theme (see CSS section)
│   ├── js/
│   │   ├── live.js           # Live trade SocketIO client + state machine UI
│   │   ├── analyzer.js       # Option chain SocketIO client + chain render
│   │   ├── oi_tracker.js     # OI tracker SocketIO client + table update
│   │   └── dashboard.js      # Dashboard 5s poller + chart updates
│   └── assets/
│       ├── darkSmall.png     # Logo for navbar (height 30px)
│       ├── darkLarge.png     # Logo for standalone pages (height 72px)
│       ├── logoLarge.png     # Alt logo variant
│       └── logoSmall.png     # Alt logo variant
│
├── trades/                   # Auto-created; one JSON file per completed trade
│   └── trade_YYYYMMDD_HHMMSS.json
│
├── data/                     # Auto-created by candle_service
│   └── candles.db            # SQLite — 5-min OHLCV for NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY
│
├── deployment/
│   ├── marketmascot.service  # Systemd unit file
│   └── nginx.conf            # Nginx reverse proxy config (handles WebSocket upgrade)
│
├── anon.session              # Telethon session (gitignored — do NOT commit)
├── runtime_config.json       # Runtime credential overrides (gitignored)
└── dhan_token.json           # Legacy token file (unused — kept for reference)
```

---

## File-by-File Reference

### `app.py` — Entry Point
- Calls `create_app()` which registers all blueprints and SocketIO events
- Starts two background daemon services: `candle_service.start()` and `notification_service.start(socketio)`
- Registers a `before_request` auth guard:
  - `/static/*` — always public
  - `PUBLIC_PATHS` set — always public (settings, auth pages)
  - `/api/*` — skips `auth_ready` check so in-page polls don't break mid-session
  - `APP_PIN` env set → redirects to `/pin` if `session["pin_ok"]` not set
  - Otherwise checks `session["auth_ready"]`; calls `both_valid()` to set it if missing; redirects to `/auth/status` if credentials bad
- Run: `python app.py` (dev) or via systemd (prod)

### `globals.py` — Environment Config
- Calls `load_dotenv()` once at import time
- Exports: `API_APP`, `API_HASH`, `APP`, `DHAN_ACCESSTOKEN`, `DHAN_CLIENTID`, `DHAN_APIKEY`, `DHAN_APISECRET`
- Also stores mutable app state: `PCR_SERIES` and `PHASE_LOG` lists used by dashboard

### `runtime_config.py` — Live Credential Override
- Reads/writes `runtime_config.json` in the project root
- `get_dhan_credentials()` → `(client_id, access_token)` — always call this, never read `globals.DHAN_ACCESSTOKEN` directly
- `save_dhan_credentials(client_id, token)` — called by settings route after form submit
- **Critical**: Dhan SDK is initialized at module import time in `dhan.py` — after updating runtime_config, a restart is needed for the SDK object to pick up new credentials

### `extensions.py` — Shared SocketIO
- Single `SocketIO()` instance imported by `app.py` (`socketio.init_app(app)`) and by route modules that need to emit
- Exists solely to break circular import: `app.py` → `routes/*` → `extensions` ← `app.py`

### `dhan.py` — Dhan SDK
- Initializes `dhan_context = DhanContext(client_id, token)` and `dhan = dhanhq(dhan_context)` at import
- Calls `dhan.get_positions()` at import time (intentional health check — logs to console)
- Downloads Dhan's instrument master CSV from `images.dhan.co` into a pandas DataFrame
- `lookup_security(symbol, strike, option_type)` — finds the nearest upcoming expiry option contract and returns `security_id, trading_symbol, expiry, lot_size, exchange_segment`
- Exchange detection: returns `"BSE_FNO"` for SENSEX/Bankex, `"NSE_FNO"` otherwise

### `tgwrap.py` — Telegram Wrapper
- Uses Telethon with a **persistent background event loop** (one daemon thread running `loop.run_forever()`)
- `_get_api_credentials()` — reads API_APP/API_HASH lazily at call time (not at import) so `.env` changes are reflected
- `_auth_client_id` tracks what `api_id` the current client was built with; `_get_client()` recreates the client if credentials changed
- `get_tips(limit)` — sync wrapper over `fetch_tips_list()` async coroutine; returns list of parsed tip dicts
- Tip dict fields: `symbol, strike, type, entry, sl, targets, raw, date, msg_id`
- `is_authorized()` — checks if saved session is still valid (used by auth health check)
- `send_code(phone)` / `complete_sign_in(phone, code, hash, password)` — Telegram auth wizard steps
- Session file: `anon.session` (gitignored); override path with `TELETHON_SESSION` env var
- Channel ID hardcoded: `-1001881641339` — change this if monitoring a different channel

### `price_feed.py` — DhanHQ WebSocket
- `start_feed(dhan_context, instruments, on_tick)` — starts MarketFeed in a daemon thread
- `instruments` format: list of `(exchange, security_id, sub_type)` tuples where exchange is `MarketFeed.NSE_FNO` etc.
- Tick dict keys: `security_id, LTP` (or `last_price`), plus full book data in Full mode
- `price_cache` dict holds latest tick per security_id, plus `"__status__"` and `"__error__"` keys
- 429 rate limit handling: pauses feed for 30s then resumes
- **Do not call `start_feed` directly** — go through `feed_manager` so multiple subscribers share one connection

### `feed_manager.py` — WebSocket Subscriber Registry
- Problem it solves: Analyzer, OI Tracker, and Dashboard all need live prices — without this, each would open its own WebSocket and fight each other
- `subscribe(owner, instruments, on_tick)` — register a named subscriber; triggers feed rebuild if instrument list changed
- `unsubscribe(owner)` — deregisters; feed rebuilt without those instruments
- Dispatches every tick to all registered `on_tick` callbacks
- **owner names used**: `"oi_tracker"`, `"analyzer"` — search these in route files to find where they subscribe

### `candle_service.py` — 5-Minute Candle Storage
- Background daemon thread fetches 5-min OHLCV from Dhan's `intraday_minute_data` API
- Fetch schedule: every 5-minute boundary + 35-second buffer (e.g., 09:20:35, 09:25:35)
- Only runs during market hours: Mon–Fri 09:14–15:36 IST
- Stores to `data/candles.db` (SQLite) — keeps last 50 candles per instrument
- Tracked instruments: NIFTY (sid=13), BANKNIFTY (sid=25), FINNIFTY (sid=27), MIDCPNIFTY (sid=442)
- `get_candles(instrument, n=50)` — returns list of dicts `{time, open, high, low, close, volume}` oldest→newest
- `get_live_candle(instrument)` — fetches 1-min bars and aggregates the current (partial) 5-min bar
- `fetch_instrument(instrument)` — on-demand single-instrument fetch (used by dashboard route)
- `start()` / `stop()` — idempotent start/stop; `start()` called from `app.py` factory

### `indicators.py` — OI Analysis Functions
- Pure functions, no I/O, stateless
- `build_oi_df(chain)` — converts Dhan option chain dict to pandas DataFrame with CE/PE OI, LTP, IV, Greeks
- `calculate_max_pain(df)` — finds strike minimizing aggregate option writer loss
- `calculate_pcr(df)` → float — Put/Call Ratio of total OI
- `classify_pcr(pcr)` → string — BULLISH / MILDLY_BULLISH / NEUTRAL / MILDLY_BEARISH / BEARISH
- `classify_oi_levels(df, spot)` — tags each strike as support/resistance/wall
- `assess_oi_clarity(df)` — confidence score for OI signal strength
- `generate_signals(df, spot, pcr, max_pain)` — list of actionable signal dicts

### `indicators_dashboard.py` — Strategy Dashboard Functions
- Pure functions, no I/O, stateless
- Candle dict schema: `{time: str, open: float, high: float, low: float, close: float, volume: int}`
- `classify_regime(candles, ema_values)` → `IMPULSE_UP | IMPULSE_DOWN | REVERSAL_WATCH | CONSOLIDATION`
- `compute_move_velocity(candles)` → float — average pts/bar momentum
- `classify_move_phase(candles, ema_values)` → `EXTENSION | PULLBACK | BASE | PAUSE`
- `compute_trend_health(candles, pcr_series)` → 0–100 score
- `compute_linear_move_score(candles)` → 0–100 how straight/linear the move is
- `detect_oi_wall(df, spot, side)` → strike — nearest CE wall (resistance) or PE wall (support)
- `build_phase_timeline(phase_log)` → list of phase segments for chart

### `notification_service.py` — In-App Notifications
- Thread-safe in-memory store (max 50 notifications, newest first)
- `notify(title, body, category, instrument, send_telegram)` — call from any module to post a notification; emits SocketIO `"notification"` event to all browser clients
- Categories: `"signal"`, `"tip"`, `"alert"`, `"system"`
- `get_all()` / `mark_read(id)` / `get_unread_count()` — API used by `routes/notifications.py`
- Background tips poller: every 60s calls `tgwrap.get_tips(limit=20)`, deduplicates by `msg_id`, surfaces new tips as notifications
- `start(sio)` — inject SocketIO instance and start poller; called from `app.py` factory

### `signal_notifier.py` — Automatic Signal Alerts
- Background daemon: checks strategy dashboard every 5 minutes during market hours
- If `_build_snapshot(instrument)` returns an ENTER signal with `score >= 70`, fires a notification
- 30-minute cooldown per instrument to avoid alert spam
- Instruments scanned: NIFTY, BANKNIFTY
- Not yet wired into `app.py` — call `signal_notifier.start()` after `notification_service.start(socketio)` to enable

---

### `routes/auth.py` — PIN Gate + Credential Health
- **`/pin`** (GET/POST): PIN entry form. Rate-limited 5 attempts/hr/IP. On success sets `session["pin_ok"]`. If no `APP_PIN` env, skips gate.
- **`/auth/status`** (GET): Shows Dhan token health (JWT exp decode) and Telegram session status. Sets/clears `session["auth_ready"]`.
- `check_dhan_token()` — decodes JWT without signature verification; checks `exp` claim against current time
- `check_tg_session()` — calls `tgwrap.is_authorized()`
- `both_valid()` — returns True only if both Dhan token and Telegram session are valid; used by `app.py` auth guard

### `routes/home.py` — Home Page
- Calls `dhan.get_positions()` to get open position count for the status badge
- Renders `home.html` with `dhan={ok: bool, count: int}`

### `routes/tips.py` — Telegram Tips
- **`/tips`**: Fetches tips via `tgwrap.get_tips(limit)` and caches in session. Renders table.
- **`/tips/refresh`**: Clears session cache and redirects back to `/tips`
- **`/tips/lookup`** (POST, JSON): Given symbol/strike/type, returns security details from instrument master
- **`/tips/execute`** (POST form): Looks up security, stores `session["watching"]` dict, redirects to `/live`

### `routes/custom.py` — Manual Trade Entry
- Form: instrument, strike, CE/PE, entry price, SL, targets, lots (auto from available balance or manual)
- On submit: calls `lookup_security()`, calculates lots from `dhan.get_fund_limits()`, stores `session["watching"]`, redirects to `/live`

### `routes/live.py` — Trade State Machine
- State machine: `idle → watching → ordering → active → exiting → idle`
- Uses `price_feed` directly (not feed_manager) — live trade is the sole subscriber during active trading
- SocketIO events: emits `"tick"` every price update, `"state_change"` on transitions
- Auto-buy when LTP crosses entry trigger; auto-sell at SL or any target
- Saves completed trade to `trades/trade_YYYYMMDD_HHMMSS.json`
- `session["watching"]` dict must contain: `security_id, trading_symbol, expiry, lot_size, exchange_segment, entry, sl, targets`

### `routes/analyzer.py` — Option Chain
- **`/analyzer`**: Renders page; instrument selected via query param `?instrument=NIFTY`
- **`/api/analyzer/chain`** (POST): Fetches full option chain from Dhan API, stores server-side in `_chain` module dict, returns serialized chain + indicators
- **`/api/analyzer/tick`**: Returns latest ticks from `price_cache` for currently loaded chain
- Uses `feed_manager.subscribe("analyzer", ...)` for live OI/LTP streaming
- SocketIO: joins rooms `az_{security_id}` per strike for targeted tick updates

### `routes/oi_tracker.py` — OI Recording
- **`/oi-tracker`**: Page render
- **`/oi-tracker/start`** (POST): Takes symbol/expiry/strikes, looks up security IDs, captures baseline OI snapshot, starts feed via `feed_manager.subscribe("oi_tracker", ...)`
- **`/oi-tracker/stop`** (POST): Unsubscribes from feed, resets state
- **`/api/oi-tracker/kpis`**: Returns current OI delta rows for table update
- `_compute_kpis()` — calculates per-strike OI delta, % change, buildup pattern (Long Buildup / Short Buildup / Long Unwinding / Short Covering)

### `routes/dashboard.py` — Strategy Dashboard
- **`/dashboard`**: Page render; passes `INSTRUMENT_NAMES` for the selector
- **`/api/dashboard/snapshot`** (GET): Full indicator JSON polled every 5s by frontend
- **`/api/dashboard/oi_map`** (GET): OI wall data polled every 30s
- `_build_snapshot(instrument)` — core function: gets candles from SQLite, computes EMA9, calls all `indicators_dashboard` functions, optionally merges live OI from oi_tracker
- `_compute_ema(candles, period=9)` — EMA of close prices; returns `None` for early bars

### `routes/history.py` — Trade History
- Reads all `trades/*.json` files, calculates total P&L, win rate, win/loss count
- Renders `history.html` with trades list and summary dict

### `routes/settings.py` — Credentials Management
- **`/settings`**: Shows current Telegram auth state and masked Dhan token
- **`/settings/tg/phone`** → **`/settings/tg/code`** → **`/settings/tg/2fa`**: Three-step Telegram auth wizard using `tgwrap.send_code()` and `tgwrap.complete_sign_in()`
- **`/settings/dhan`** (POST): Calls `save_dhan_credentials()` then redirects to `/settings/restarting`
- **`/settings/restarting`** (GET): Restarts the Python process via `os.execv(sys.executable, ...)`
- After successful Telegram auth: redirects to `/auth/status` (not home) so health is re-checked

### `routes/notifications.py` — Notification API
- **`/api/notifications`** (GET): Returns all notifications from `notification_service.get_all()`
- **`/api/notifications/read`** (POST): Marks notification(s) as read

---

### Templates

All templates extend `templates/base.html` except three standalone pages.

**`base.html`** — Shared layout
- Bootstrap 5.3.3 dark theme (`data-bs-theme="dark"`)
- Inter font from Google Fonts
- Loads `static/css/style.css` (glassmorphism theme)
- Navbar: `darkSmall.png` logo + "MarketMascot" brand + notification bell dropdown
- Bell badge shows unread count; dropdown lists last 5 notifications
- SocketIO client JS (`socket.io.min.js` from CDN)
- Listens for `"notification"` SocketIO event — updates bell badge and prepends to dropdown
- Blocks: `title`, `content`, `scripts`

**`home.html`** — Feature card grid (6 cards: Live Tips, Custom Trade, Option Analyzer, Strategy Dashboard, Trade History, Settings)

**`tips.html`** — Tips table inside a dark glass card; click a row to expand detail panel with instrument metrics, raw message, and Execute button; uses `TIPS` JS array injected from Jinja

**`custom.html`** — Manual trade form with instrument dropdown, strike, CE/PE, entry/SL/target fields, lots mode radio

**`live.html`** — Live price display, state banner (watching/ordering/active/exiting), P&L meter, exit button; state driven by SocketIO `"tick"` and `"state_change"` events; JS in `static/js/live.js`

**`analyzer.html`** — Instrument selector, option chain table (sortable by strike), ATM row highlighted; live updates via SocketIO; JS in `static/js/analyzer.js`

**`oi_tracker.html`** — Strike selector, start/stop controls, live OI delta table; JS in `static/js/oi_tracker.js`

**`dashboard.html`** — Instrument selector, regime badge, phase badge, velocity, trend health bar, linear score, OI walls, phase timeline chart; JS in `static/js/dashboard.js` polls `/api/dashboard/snapshot` every 5s

**`history.html`** — Trades table (symbol, entry, exit, P&L) + summary metrics (total trades, net P&L, win rate)

**`settings.html`** — Telegram auth wizard (shows phone/code/2FA step based on `session["tg_step"]`) + Dhan token form

**`auth_status.html`** — Standalone page (no base.html). Shows Dhan and Telegram status cards side by side. "Enter App →" button only shown when both valid.

**`pin.html`** — Standalone page. PIN input form with error display and logo.

**`restarting.html`** — Standalone page. Spinner + countdown; polls `/` every second after 4s, redirects to `/settings` when server responds.

---

### Static Assets

**`static/css/style.css`** — Full glassmorphism theme
- CSS custom properties: `--glass-bg`, `--glass-border`, `--glass-blur`, `--glass-radius`, `--purple-*`, `--orange-*`, `--on-glass`, `--live-green`
- Body: light gradient (`#fafafa → #f5f0ff → #fff4f0`) with radial purple/orange blob decorations via `body::before`
- Cards: dark glass (`rgba(12,4,28,0.76)`, `backdrop-filter: blur(16px)`)
- Text philosophy:
  - Page-level: dark purple text (`#2d1245`)
  - Inside `.card *`: white text via `var(--on-glass)`
  - `.text-secondary`: `#7c6590` on page, `var(--on-glass-muted)` inside cards
- Form controls: dark text on page, white text inside `.card .form-control`
- Buttons: `.btn-outline-secondary` dark purple on page, light inside `.card`
- Alerts: all use dark readable text regardless of context
- `--live-green` (`#00e676`) is the **only** green in the theme — used solely for the live dot and Dhan online badge
- Color palette: deep purple + deep orange accents only

**`static/js/live.js`** — Live trade SocketIO client; handles tick display, state banner color changes, auto-scroll P&L log

**`static/js/analyzer.js`** — Fetches chain on load, subscribes to SocketIO tick rooms, updates OI/LTP cells in place

**`static/js/oi_tracker.js`** — Polls `/api/oi-tracker/kpis` every 2s; updates table rows with color-coded OI deltas

**`static/js/dashboard.js`** — Polls `/api/dashboard/snapshot` every 5s; updates all metric displays and chart

---

### Deployment Files

**`deployment/marketmascot.service`** — Systemd unit
- `ExecStart`: runs `python app.py` in the virtualenv
- `WorkingDirectory`: `/opt/marketmascot`
- `EnvironmentFile`: `/opt/marketmascot/.env`
- `Restart=always`, `RestartSec=5`

**`deployment/nginx.conf`** — Nginx reverse proxy
- Proxies HTTP → `127.0.0.1:5000`
- WebSocket upgrade headers for SocketIO (`Upgrade`, `Connection`)
- Serve static files directly from `static/` for performance

---

## Key Architecture Decisions

### Single-user design
`_trade`, `_chain`, `_tracker` are module-level dicts — not per-user/session. This is intentional for a personal trading tool. SaaS hosting means one person uses it at a time, protected by the PIN gate.

### Credential layering
```
runtime_config.json  (Settings page writes here — highest priority)
    ↓ falls through to
.env                 (loaded by python-dotenv at startup)
    ↓ falls through to
hardcoded defaults   (empty strings)
```
Always use `get_dhan_credentials()` from `runtime_config.py`. Never read `globals.DHAN_ACCESSTOKEN` directly in any auth or API code.

### Telethon persistent loop
Telethon creates internal tasks on the event loop it is started on. Creating a new loop per call would corrupt those tasks. `tgwrap.py` uses one daemon thread running `loop.run_forever()` for all Telegram calls.

### WebSocket sharing (feed_manager)
DhanHQ MarketFeed supports one active connection. Multiple features (Analyzer, OI Tracker, Dashboard) compete for it. `feed_manager.py` holds the canonical subscriber registry and restarts the feed only when the merged instrument list changes.

### Candle storage (SQLite)
`candle_service.py` writes to `data/candles.db`. Dashboard reads from it. They never share a write lock at the same time because only one writer exists. `UNIQUE(instrument, interval, time)` constraint ensures idempotent inserts (`INSERT OR IGNORE`).

### SocketIO threading mode
`async_mode="threading"` — no asyncio event loop in Flask. All background threads use standard `threading.Thread`. Telethon has its own loop in a separate thread.

---

## Data Flow Diagrams

### Tip → Trade Flow
```
Telegram channel
    → tgwrap.get_tips()
    → /tips page (table)
    → user clicks Execute
    → /tips/execute → session["watching"]
    → redirect /live
    → live.js subscribes SocketIO
    → price_feed watches security_id
    → tick crosses entry → place_order() → Dhan API
    → tick crosses SL/target → exit_order() → Dhan API
    → save trade JSON
```

### Dashboard Data Flow
```
Dhan intraday_minute_data API
    → candle_service (every 5min)
    → data/candles.db (SQLite)
    → /api/dashboard/snapshot (every 5s)
    → indicators_dashboard functions
    → JSON response
    → dashboard.js updates DOM
```

### Live Price Flow
```
DhanHQ MarketFeed WebSocket
    → price_feed._on_message()
    → price_cache[security_id] = tick
    → on_tick callback
    → feed_manager._dispatch()
    → each subscriber's on_tick()
        → analyzer route → SocketIO emit to browser
        → oi_tracker route → update _tracker["current"]
```

---

## Common Modification Patterns

### Add a new tracked index instrument
1. `candle_service.py` → add to `TRACKED` list with `name`, `security_id`, `exchange`
2. `routes/analyzer.py` → add to `INDICES` dict with `security_id`, `lot_size`, `exchange`
3. `signal_notifier.py` → add to `INSTRUMENTS` list

### Add a new page/route
1. Create `routes/yourpage.py` with a Blueprint `bp`
2. Create `templates/yourpage.html` extending `base.html`
3. Register in `app.py`: `import routes.yourpage as yourpage_mod` + `app.register_blueprint(yourpage_mod.bp)`

### Add a new notification category
1. Add category string to `CATEGORIES` set in `notification_service.py`
2. Call `notify(title, body, category="your_category")` from any module

### Extend Telegram tip parsing
Edit `tgwrap.parse_tip(text)` — regex-based extraction of fields from raw message text.

### Change the Telegram channel
Edit `CHANNEL_ID` in `tgwrap.py` — currently `-1001881641339`.

---

## Known Limitations

1. **Single-user**: No per-user sessions for trade state. If two browser tabs are open, they share the same trade state.
2. **Dhan SDK re-init**: `dhan.py` initializes the SDK at import time. Updating credentials via Settings writes to `runtime_config.json` but the SDK object in memory still uses old credentials until restart. The Settings → Dhan update flow triggers a restart for this reason.
3. **Telethon session file**: `anon.session` must be present for Telegram to work. On a fresh server, go through the auth wizard at `/settings` once. After that the session persists across restarts.
4. **Market hours only**: Candle service and signal notifier are no-ops outside 09:15–15:30 IST Mon–Fri.
5. **No database for trades**: Trade history is flat JSON files in `trades/`. No aggregation beyond what `history.py` computes on each page load.
