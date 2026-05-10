# GPredict-Improved

🌐 [日本語](README.ja.md) | English

**Modern successor to GPredict** — Amateur satellite tracking software

[![CI](https://github.com/JF9SOM/gpredict-improved/actions/workflows/ci.yml/badge.svg)](https://github.com/JF9SOM/gpredict-improved/actions)
[![License: GPL v2](https://img.shields.io/badge/License-GPL%20v2-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)

GPredict-Improved is a ground-up rewrite of
[GPredict](https://github.com/csete/gpredict) — the beloved amateur radio
satellite tracker by Alexandru Csete OZ9AEC — built on a modern Python stack.

---

## What's improved

| Feature | GPredict | GPredict-Improved |
|---------|----------|-------------------|
| Platform | Desktop only | Desktop + **browser access from phones/tablets on the same LAN** |
| Radio control | Requires separate rigctld | **Built-in Hamlib** — select your radio in the GUI |
| Doppler correction | Frequency only | **Frequency + mode + CTCSS tone** set automatically |
| Satellite frequency DB | SATNOGS only, text-file editing | SATNOGS auto-sync + **add/edit entries from the GUI** |
| TLE updates | Manual | **Multi-source auto-update with quality scoring** |
| Supported OS | Linux (GTK+) | **Linux, Windows, macOS, Raspberry Pi** |

---

## Installation (Ubuntu/Debian)

```bash
# 1. System packages
sudo apt install python3.11 python3-pip libhamlib-dev python3-hamlib

# 2. Clone
git clone https://github.com/JF9SOM/gpredict-improved.git
cd gpredict-improved

# 3. Python virtual environment
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .

# 4. USB radio permissions (udev rule)
sudo cp scripts/99-gpredict-improved.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo usermod -aG dialout $USER
# Log out and back in to apply group membership

# 5. Run
gpredict-improved
```

### Windows / macOS

Pre-built installers will be available on the
[Releases](https://github.com/JF9SOM/gpredict-improved/releases) page once
the first tagged version is published.

---

## Architecture

```
gpredict-improved/
├── src/
│   ├── core/     # Satellite engine (Skyfield) — elevation, Doppler, pass prediction
│   ├── ui/       # PySide6 Qt6 desktop UI
│   ├── web/      # FastAPI + WebSocket (LAN browser access on port 8080)
│   ├── rig/      # Hamlib radio/rotator control
│   ├── data/     # TLE/SATNOGS sync, SQLite DB, manual entries
│   └── i18n/     # Internationalization (gettext-based)
├── locale/
│   ├── en/LC_MESSAGES/   # English strings
│   └── ja/LC_MESSAGES/   # Japanese strings
└── tests/
```

On startup:
1. Qt6 main window launches
2. FastAPI/uvicorn starts in a background thread (port 8080)
3. `DataSyncManager` fetches TLE and SATNOGS data if stale
4. Status bar shows the LAN URL and a QR-code button

---

## Development setup

```bash
pip install -e ".[dev]"
pytest              # run tests
ruff check .        # lint
mypy src/           # type check

# Recompile translations after editing .po files
msgfmt locale/en/LC_MESSAGES/gpredict_improved.po \
      -o locale/en/LC_MESSAGES/gpredict_improved.mo
msgfmt locale/ja/LC_MESSAGES/gpredict_improved.po \
      -o locale/ja/LC_MESSAGES/gpredict_improved.mo
```

See [CLAUDE.md](CLAUDE.md) for the full architecture reference used by Claude
Code during development.

---

## Adding a new language

1. Copy `locale/en/LC_MESSAGES/gpredict_improved.po` to
   `locale/<lang>/LC_MESSAGES/gpredict_improved.po`
2. Translate the `msgstr` lines
3. Compile: `msgfmt locale/<lang>/LC_MESSAGES/gpredict_improved.po -o locale/<lang>/LC_MESSAGES/gpredict_improved.mo`
4. The new language will appear automatically in the Settings dialog

---

## License

GPL-2.0-or-later (compatible with GPredict)

---

## Acknowledgements

- [GPredict](https://github.com/csete/gpredict) — Alexandru Csete OZ9AEC
- [Skyfield](https://rhodesmill.org/skyfield/) — Brandon Rhodes
- [Hamlib](https://hamlib.github.io/) — Hamlib Development Team
- [SATNOGS](https://satnogs.org/) — Libre Space Foundation
