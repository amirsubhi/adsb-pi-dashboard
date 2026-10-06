# Contributing

Thanks for helping. Bug reports, fixes and ideas are all welcome. Please open
an issue first for anything large, so we can agree on the approach.

## Setting up

There is nothing to install: the server is standard-library Python and the
pages are plain HTML, CSS and JavaScript.

```bash
git clone https://github.com/amirsubhi/adsb-pi-dashboard.git
cd adsb-pi-dashboard

# A simulated receiver, so you don't need an antenna
python3 tools/fake_readsb.py --dir /tmp/fake-readsb &

# The dashboard, using the files in this folder
ADSB_DATA_DIR="$PWD" ADSB_READSB_DIR=/tmp/fake-readsb python3 app.py
# open http://localhost:8099/  (and /map, /settings)
```

`fake_readsb.py` takes `--lat`, `--lon`, `--count` and `--no-location`.

## Before you open a pull request

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile app.py tools/fake_readsb.py
shellcheck install.sh uninstall.sh
```

CI runs the same checks on Python 3.9, 3.11 and 3.13. Please add a test for
any parser, setting or endpoint you add or change, and check pages in light
and dark themes and at phone width.

## Project rules

These keep the dashboard small, safe and working offline:

- **Standard library only** in `app.py`; no pip dependencies.
- **No CDNs.** Third-party frontend code is bundled under `vendor/` with its
  licence, and listed with version and SHA-256 in `THIRD_PARTY_NOTICES.md`.
- **No inline scripts** in the HTML pages. The Content Security Policy blocks
  them, and a test checks for them. Scripts go in `static/` and need an entry
  in `STATIC_FILES` in `app.py`.
- **No endpoints that change state** (settings, deletes, restarts) unless
  authentication and CSRF protection come first. The dashboard has no login.
- **Escape** anything from the receiver, feeders or the journal before it
  goes into `innerHTML`.
- **New settings** go in `SETTINGS` in `app.py`, `settings.example.ini` (a test
  checks) and the table in `README.md`.
- **Database changes** need a new `SCHEMA_VERSION` and a migration step in
  `init_db()` that upgrades existing databases in place, with a test.
- **New files the server needs** must be added to the copy step in
  `install.sh`.

`CLAUDE.md` has a fuller tour of how the code fits together.

## Writing style

Plain, direct language in the interface and docs: say what something is and
what to do, in the words a station owner would use. Units are the aviation
ones: feet, knots, nautical miles, flight levels.
