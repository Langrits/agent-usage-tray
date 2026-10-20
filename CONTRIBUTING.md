# Contributing

Bug reports and new services are welcome. Code comments are mostly in Korean; English is fine in pull requests.

## Project layout

```
run.pyw                     entry point (tray app, no console)
agent_usage/
  providers/                one file per service
    _util.py                shared helpers (Options, LoginProblem, epoch, jwt_claims, write_json_atomic)
    claude.py codex.py cursor.py gemini.py
    __init__.py             ALL = [...] registry, fetch_all()
  models.py                 Usage / Window / ResetCredits
  app.py                    tray icon + popup (updates values in place, no flicker)
  settings_window.py        settings UI
  graph.py                  usage history chart
  notify.py                 low / reset / reset-credit alerts
  i18n.py                   Korean and English strings
  store.py                  settings.json, history.sqlite
tools/probe.py              print a redacted response from a usage endpoint
tools/snapshot.py           render popup / chart / settings to PNG (use --fake for screenshots)
```

## Adding a service

1. Create `agent_usage/providers/<id>.py` with:

   ```python
   ID = "example"          # stable key used in settings and history; never change it
   TITLE = "Example"       # display name
   EXPERIMENTAL = False    # True shows a "beta" tag

   def detect() -> bool:
       """True if this PC has a login for the service. Local check only, no network."""

   def fetch(opts: Options) -> Usage:
       """Return a Usage. On a login problem return usage.fail(error, hint) instead of raising."""
   ```

   - One `Window` per limit: a stable `key` (used for history and alerts), a translated `label`, `used_pct` (0–100) and `resets_at` (epoch seconds).
   - Set `reset_credits` only if the service has them. Leave it `None` if they don't apply.
   - Unexpected exceptions are caught by `fetch_one()` and shown on the card.

2. Add the module to `ALL` in `agent_usage/providers/__init__.py`.
3. Add any new strings to both `KO` and `EN` in `agent_usage/i18n.py`.
4. Optionally give its main limits fixed chart colors in `theme.SERIES_SLOT`.
5. Test with `.venv\Scripts\python.exe -m agent_usage <id>`.

## Rules for handling credentials

- Never print, log or store tokens. `tools/probe.py` redacts long strings and emails; keep it that way.
- Send a token only to the service that issued it.
- Prefer read-only access. If you must write back a renewed token, re-read the file first, then write atomically (`write_json_atomic`), and respect `opts.auto_refresh_login`.
- Don't commit client secrets, even "public" ones. Look them up from the user's installed tool at runtime (see `gemini.py`).
- Handle `401` (expired login) and `429` (rate limited) with a clear message and hint.
