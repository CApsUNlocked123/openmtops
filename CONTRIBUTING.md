# Contributing to OpenMTOps

Thanks for your interest in contributing. This is a **single-user, self-hosted** trading assistant for Indian F&O. Keep that constraint in mind — contributions that add multi-user state, external databases, or SaaS billing are out of scope.

---

## Running in development

```bash
# No broker credentials needed — dummy data mode
TESTING=1 python app.py
```

Setting `TESTING=1` injects mock modules for `dhan_broker`, `price_feed`, and `candle_service`. The setup wizard and auth guard are also bypassed. All other routes work normally.

For full end-to-end testing you need real Dhan and Telegram credentials. Run the setup wizard at `/setup` after starting the app for the first time.

---

## Adding a route

The app uses Flask blueprints. Three steps:

1. Create `routes/yourpage.py` with a `Blueprint` named `bp`.
2. Create `templates/yourpage.html` extending `base.html`.
3. In `app.py`, add `import routes.yourpage as yourpage_mod` and `app.register_blueprint(yourpage_mod.bp)`.

If your route needs SocketIO events, add a `register_socketio(sio)` function to your module and call it from `app.py` alongside the existing ones.

---

## Configuration system

All credentials flow through `runtime_config.py`:

- `get(key)` — dotted key lookup (e.g., `"telegram.api_id"`) with `.env` fallback
- `set_many(updates)` — batch write to `config.json`
- `flush_to_dotenv()` — sync `config.json` → `.env` for restart persistence

Never read `os.getenv("DHAN_CLIENTID")` directly in route code. Always go through `get_dhan_credentials()` or `get()`.

---

## Code style

- PEP 8, 4-space indentation, max line length ~120.
- No type annotations required (the codebase does not use them).
- No docstrings required unless logic is genuinely non-obvious.
- Avoid adding error handling for scenarios that cannot happen.

---

## Architecture constraints

- **Single-user state**: `_trade`, `_chain`, `_tracker` are intentional module-level dicts. Do not convert them to per-session storage.
- **Market hours hardcoded to IST**: `candle_service.py` and `signal_notifier.py` only run 09:15–15:30 Mon–Fri IST. This is intentional for Indian F&O.
- **Instrument IDs are Dhan constants**: `NIFTY=13`, `BANKNIFTY=25`, etc. are Dhan platform constants, not configurable by users.
- **SocketIO threading mode**: The app uses `async_mode="threading"`. Do not introduce asyncio into Flask route code.

---

## Reporting issues

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE). By contributing, you agree your changes are released under the same license.

Please open an issue at https://github.com/CApsUNlocked123/openmtops/issues with:
- Your Python version and OS
- The full traceback (check terminal output)
- Whether you are in `TESTING=1` mode or using real credentials
