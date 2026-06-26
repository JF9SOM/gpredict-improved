# FBSAT59

🌐 [日本語](README.ja.md) | English

**Modern successor to GPredict** — Amateur satellite tracking software

[![CI](https://github.com/JF9SOM/fbsat59/actions/workflows/ci.yml/badge.svg)](https://github.com/JF9SOM/fbsat59/actions)
[![Release](https://img.shields.io/github/v/release/JF9SOM/fbsat59)](https://github.com/JF9SOM/fbsat59/releases/latest)
[![License: GPL v2](https://img.shields.io/badge/License-GPL%20v2-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)

FBSAT59 is a ground-up rewrite of
[GPredict](https://github.com/csete/gpredict) — the beloved amateur radio
satellite tracker by Alexandru Csete OZ9AEC — built on a modern Python stack.

---

## What's improved

| Feature | GPredict | FBSAT59 |
|---------|----------|-------------------|
| Platform | Desktop only | Desktop + **browser access from phones/tablets on the same LAN** |
| Radio control | Requires separate rigctld | **Built-in Hamlib** (700+ radios) — select your rig in the GUI |
| SDR support | None | **HackRF / RTL-SDR via SoapySDR** — spectrum, demodulation, IQ recording |
| Doppler correction | Frequency only | **Frequency + mode + CTCSS/DCS tone** set automatically |
| Dual-rig | Supported | **Rig 1 + Rig 2** — SDR dongle can be assigned as a rig |
| Satellite frequency DB | SATNOGS only, text-file editing | SATNOGS auto-sync + **add/edit entries from the GUI** |
| TLE updates | Auto-update supported | **Multi-source auto-update with quality scoring** |
| Pass prediction | List view | **Graphical pass chart + sky radar + footprint on world map** |
| Rotator control | Separate rotctld | **Built-in Hamlib rotator — select from GUI, no rotctld needed** |
| Supported OS | Linux, Windows, macOS (GTK+) | **Linux, Windows, macOS, Raspberry Pi** |

---

## Key Features

### Desktop UI (Qt6)
- **Dashboard** — zoomed world map + radar + live status bar in one view
- **World map** with satellite footprints, ground tracks, and dot-click selection
- **Radar (sky view)** — north-up, AOS/LOS times, multi-satellite colour-coded
- **Pass chart** — graphical elevation curve with quality colour coding (excellent/good/fair/low)
- **Group Pass Chart** — multi-satellite pass overview with hover tooltips
- **Upcoming Passes** — target or group search, calendar picker, CSV export
- **Radio Control** — Doppler correction, mode/CTCSS auto-set, transponder list
- **SDR Control** — real-time spectrum analyser, NFM/USB/LSB/CW demodulation, IQ recorder, passband tuning with transponder lock
- **Autotrack/Record** — automatic sequential satellite tracking with scheduled timer (start/stop time), auto rig+rotator connect at AOS / disconnect at LOS, and automatic SDR audio/IQ recording between AOS and LOS
- **AOS/LOS desktop notifications** (Linux: notify-send / macOS: osascript / Windows: PowerShell)

### Communications (Digital Modes)
Access via the **Communications** menu (between Radio and Autotrack/Record). Each mode opens as a closeable non-resident tab.

- **APRS** — receive and decode AX.25/APRS packets via Direwolf (TCP KISS) with a Rig + sound card, or via the built-in Bell 202 AFSK demodulator when an SDR is connected. Send APRS messages and position beacons (PTT via CAT). Received position packets appear as cyan ▲ pins on the Dashboard map. Callsign, SSID, and via path are saved. ADIF export.
- **Telemetry** — decode AX.25 telemetry frames from amateur satellites. Binary format definitions included for 12 satellites (ISS, FO-29, SO-50, AO-73, JO-97, RS-44, MO-122, etc.). Raw hex display for undefined satellites. CSV export.
- **SSTV / SSDV** — receive SSTV images (Robot36, PD120, Martin, Scottie) and SSDV packets from amateur satellites (e.g. ISS 145.800 MHz PD120 or 437.550 MHz Robot36). Works with SDR audio or a rig sound card. Auto-opens when a transponder description contains "SSTV", "SSDV", or "IMAGING".
- **FT4** — encode and decode FT4 using the built-in ft8_lib (ctypes — no WSJT-X required). Transmit via Rig + PTT. Auto-opens for RS-44, JO-97, MO-122, and other FT4-active satellites. ADIF export.
- **Help → Direwolf Installation…** — detect, install, or update Direwolf on all platforms
- **Help → gr-satellites…** — detect gr-satellites installation and show install instructions (apt / brew / pip)

### Mobile Browser UI
Access from any smartphone or tablet on your local network — no app install needed.

- **Tracking tab** — satellite list with live EL/AZ/Range + radar
- **Antenna tab** — large AZ/EL readout, pass progress bar, transponder cards, remote RIG connect/disconnect
- **Pass Prediction tab** — upcoming passes per satellite
- **Group Pass tab** — search and display passes for a group
- **Compass-linked radar** on Android (auto-rotates with device orientation)

### Radio / Rotator Control
- Hamlib 4.7.1 built-in — no separate rigctld needed
- NET Control mode (rigctld/rotctld compatible) for existing setups
- Dual-rig: Rig 1 + Rig 2 independent control (e.g. IC-9700 + HackRF)
- Inverted transponder support with passband tuning
- Catch-up rotator tracking with configurable timeout resend

### Data Management
- **SATNOGS** transmitter DB auto-sync (daily)
- **Community frequency DB** — FT4 calling frequencies and other conventions not in SATNOGS
- **TLE multi-source**: CelesTrak Amateur/CubeSat/Weather/Earth-Obs/Science/Stations, SATNOGS TLE API, manual entry
- TLE quality scoring: excellent (<6 h) / good (<24 h) / fair (<72 h) / poor
- Provisional NORAD IDs (90000-series) auto-resolved to real IDs
- Manual TLE and transponder entries — never overwritten by auto-sync
- Custom Favourite groups (configurable names, up to N groups)

### Auto Fetch Schedule

FBSAT59 fetches TLE and transponder data automatically in the background.
**Manual updates are not normally required.**
Use manual sync only when you need the very latest data immediately (e.g. right before a pass of a newly launched satellite).

| Data | Interval |
|---|---|
| Space Stations (ISS, CSS…) | every **1 hour** |
| Amateur Satellites | every **2 hours** |
| CubeSats | every **4 hours** |
| Weather Satellites | every **6 hours** |
| Earth Observation / Science | every **12 hours** |
| Provisional TLEs (NORAD ≥ 90000) | every **12 hours** |
| Active TLE fallback (NORAD 10000–89999) | every **24 hours** |
| AMSAT operational status | every **24 hours** |

SATNOGS transponder data is fetched automatically on first launch.
After that, use **Satellite → Sync SATNOGS** to refresh manually if needed.
A summary is also available in the app under **Help → Auto Fetch Rules**.

### In-app Updaters
- **Help → Check for Updates** — downloads and installs the latest release automatically
- **Help → Hamlib Update** — upgrades the bundled Hamlib without reinstalling the app

---

## Installation

### Windows

Download `FBSAT59-Setup.exe` from the
[Releases](https://github.com/JF9SOM/fbsat59/releases/latest) page
and run the installer.

### macOS

Download `FBSAT59.dmg` from the
[Releases](https://github.com/JF9SOM/fbsat59/releases/latest) page,
open it, and drag the app to Applications.

### Linux (AppImage)

```bash
# Download and make executable
chmod +x FBSAT59-*.AppImage
./FBSAT59-*.AppImage
```

### Linux (from source — Ubuntu/Debian)

```bash
# 1. System packages
sudo apt install python3.11 python3-pip libhamlib-dev python3-hamlib \
                 python3-soapysdr soapysdr-module-rtlsdr soapysdr-module-hackrf

# 2. Clone
git clone https://github.com/JF9SOM/fbsat59.git
cd fbsat59

# 3. Python virtual environment
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .

# 4. USB radio permissions (udev rule)
sudo cp scripts/99-fbsat59.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo usermod -aG dialout $USER
# Log out and back in to apply group membership

# 5. Run
python -m src.main
```

---

## SDR Quick Start

1. Connect your SDR device (HackRF One, RTL-SDR, etc.)
2. Open **Settings → Rig Settings → SDR Settings**
3. Click **Enumerate** to detect devices
4. Select your device, set sample rate and gain, assign to Rig 1 or Rig 2
5. Click **Connect** — the **SDR Control** tab becomes active
6. Select a satellite and transponder — mode is set automatically

### SDR — Platform Support

| Platform | SDR support |
|----------|-------------|
| **Windows** | ✅ RTL-SDR, HackRF One (ctypes direct — WinUSB driver via Zadig required) |
| **Linux** | ✅ All SoapySDR-compatible devices (system package install) |
| **macOS** | ✅ All SoapySDR-compatible devices (Homebrew install) |

**Windows** — On Windows, SoapySDR is fundamentally incompatible with WinUSB drivers
and cannot open devices reliably. RTL-SDR and HackRF bypass SoapySDR entirely and
communicate directly with the device DLL (`librtlsdr.dll` / `hackrf.dll`) via ctypes.
**Both RTL-SDR and HackRF require a one-time WinUSB driver setup with Zadig.**
Airspy, Airspy HF+, and ADALM-Pluto are **not supported on Windows**.

> ⚠️ **Windows Zadig setup (RTL-SDR and HackRF)**
> 1. Plug in your device.
> 2. Download and run [Zadig](https://zadig.akeo.ie/) (free).
> 3. In Zadig: **Options → List All Devices**, select your device
>    (RTL-SDR: *Bulk-In, Interface 0* / HackRF: *Hackrf One*).
>    Set driver to **WinUSB** → click **Install Driver**.
>    **Do NOT select libusbK** — it causes device detection failures.
> 4. Restart FBSAT59.
>
> See also **Help → SDR Device Installation** for step-by-step guidance.

**Linux** — install via apt:
```bash
sudo apt install python3-soapysdr soapysdr-module-rtlsdr soapysdr-module-hackrf \
                 soapysdr-module-airspy
```

**macOS** — install via Homebrew:
```bash
brew install soapysdr soapyrtlsdr soapyhackrf soapyairspy
```

**Linux** — install via apt:
```bash
sudo apt install python3-soapysdr soapysdr-module-rtlsdr soapysdr-module-hackrf \
                 soapysdr-module-airspy
```

**macOS** — install via Homebrew:
```bash
brew install soapysdr soapyrtlsdr soapyhackrf soapyairspy
```

> Other SoapySDR-compatible devices (LimeSDR, etc.) may work on Linux/macOS
> if the corresponding module is installed, but are not bundled on Windows.

> **SDRplay (RSP1, RSP2, RSPdx, etc.)** — Not bundled on any platform because SoapySDRPlay3
> depends on the proprietary SDRplay API library, which cannot be redistributed.
>
> To use an SDRplay device (all platforms):
> 1. Install the **SDRplay API** from [sdrplay.com/downloads](https://www.sdrplay.com/downloads/)
>    (Windows/macOS installer or Linux `.run` script)
> 2. Install **SoapySDRPlay3**:
>    - Linux: `sudo apt install soapysdr-module-sdrplay3`
>    - macOS: build from source or `conda install -c conda-forge soapysdr-module-sdrplay3`
>    - Windows: build from [github.com/pothosware/SoapySDRPlay3](https://github.com/pothosware/SoapySDRPlay3) or use conda
> 3. Restart this software — your device will be detected automatically via SoapySDR.

> **ADALM-Pluto (PlutoSDR)** — Not bundled on any platform (Windows CI build was unstable;
> Linux/macOS users must install manually). Available on all platforms via package managers.
>
> **How PlutoSDR networking works:** when connected via USB, PlutoSDR creates a virtual Ethernet
> adapter. No special driver (Zadig / WinUSB) is needed on any platform. The device is reachable
> at IP address **192.168.2.1**.
>
> To use ADALM-Pluto (all platforms):
> 1. Connect PlutoSDR via USB (the USB network adapter is installed automatically).
> 2. Install **libiio**:
>    - Linux: `sudo apt install libiio-dev`
>    - macOS: `brew install libiio`
>    - Windows: installer from [github.com/analogdevicesinc/libiio/releases](https://github.com/analogdevicesinc/libiio/releases)
> 3. Install **SoapyPlutoSDR**:
>    - Linux: `sudo apt install soapysdr-module-plutosdr`
>    - macOS: `brew install soapyplutosdr` or `conda install -c conda-forge soapysdr-module-plutosdr`
>    - Windows: `conda install -c conda-forge soapysdr-module-plutosdr` or build from [github.com/pothosware/SoapyPlutoSDR](https://github.com/pothosware/SoapyPlutoSDR)
> 4. Restart this software — PlutoSDR will be detected automatically.

---

## Architecture

```
fbsat59/
├── src/
│   ├── core/     # Satellite engine (Skyfield) — elevation, Doppler, pass prediction
│   ├── ui/       # PySide6 Qt6 desktop UI
│   ├── web/      # FastAPI + WebSocket (LAN browser access on port 8080)
│   ├── rig/      # Hamlib radio/rotator control + SdrRigAdapter
│   ├── sdr/      # SoapySDR backend — device, pipeline, demodulator, recorder
│   ├── comms/    # Digital communications — APRS engine, Direwolf, Bell 202 AFSK, AX.25
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
4. Status bar shows the LAN URL and a QR-code button for mobile access

---

## Development Setup

```bash
pip install -e ".[dev]"

# Format
ruff format src/ tests/

# Lint
ruff check src/ tests/

# Type check
mypy --strict src/

# Tests (run only test_rig.py locally — full suite may be slow on low-power hardware)
python -m pytest tests/test_rig.py -q

# Recompile translations after editing .po files
msgfmt locale/ja/LC_MESSAGES/fbsat59.po \
      -o locale/ja/LC_MESSAGES/fbsat59.mo
```

See [CLAUDE.md](CLAUDE.md) for the full architecture reference used during development.

---

## Hardware Verified

| Device | Type | Windows | Linux/macOS | Notes |
|--------|------|---------|-------------|-------|
| Yaesu FTX-1F | Transceiver | ✓ | ✓ | Hamlib 4.7.1 model 1051, NET Control, Doppler |
| Yaesu FT-991AM | Transceiver | ✓ | ✓ | Hamlib 4.7.1 model 1036, NET Control, Doppler |
| Icom IC-9100 | Transceiver | — | ✓ | Hamlib 4.7.1 model 3068, NET+Direct, SAT mode, Doppler (v0.1.27) |
| Icom IC-9700 | Transceiver | ✓ | ✓ | Hamlib 4.7.1 model 3081, NET+Direct, SAT mode, Doppler (v0.1.27) |
| RTL-SDR | SDR | ✓ (WinUSB/Zadig)* | ✓ | ctypes direct on Windows, SoapyRTLSDR on Linux/macOS |
| HackRF One | SDR | ✓ (WinUSB/Zadig)* | ✓ | ctypes direct on Windows, SoapyHackRF on Linux/macOS |
| Airspy R2 / Mini | SDR | ❌ not supported | ✓ | SoapyAirspy (Linux/macOS only) |
| Airspy HF+ | SDR | ❌ not supported | ✓ | SoapyAirspyHF (Linux/macOS only) |
| ADALM-Pluto | SDR | ❌ not supported | ✓ | SoapyPlutoSDR (Linux/macOS only) |
| FTX-1F + RTL-SDR | Dual-rig | ✓ | ✓ | Passband Tune + Lock verified |

\* Windows: both RTL-SDR and HackRF require a one-time WinUSB driver install via Zadig.
SoapySDR is incompatible with WinUSB on Windows; RTL-SDR and HackRF bypass it via ctypes.

---

## Adding a New Language

1. Copy `locale/en/LC_MESSAGES/fbsat59.po` to
   `locale/<lang>/LC_MESSAGES/fbsat59.po`
2. Translate the `msgstr` lines
3. Compile: `msgfmt locale/<lang>/LC_MESSAGES/fbsat59.po -o locale/<lang>/LC_MESSAGES/fbsat59.mo`
4. The new language will appear automatically in the Settings dialog

---

## Roadmap

### Phase 2 — Planned

#### Digital Modes — Amateur Satellites (SDR)
- **HRPT / LRPT** — weather satellite image reception via SatDump
- **CW decode** — AI-based decoder (ML inference, no zero-crossing artefacts)
- **gr-satellites deep integration** — 100+ satellite telemetry formats via gr-satellites subprocess

#### Operational Satellite Reception (SDR) — Planned
Receivable with HackRF / RTL-SDR + appropriate LNA/filter. Open-source decoders exist for all of these and will be integrated as SDR plugins.

| System | Band | Content | OSS Decoder |
|---|---|---|---|
| **Inmarsat-C (STD-C)** | 1.5 GHz L-band | Maritime Safety Info (MSI), EGC, LRIT | [JAERO](https://github.com/jontio/JAERO) |
| **Cospas-Sarsat L-band** | 1544.5 MHz | Search & rescue beacon positions (PLB/EPIRB/ELT) | gr-satellites |
| **Iridium L-band ACARS** | 1616–1626.5 MHz | Aviation ACARS messages over Iridium | [iridium-toolkit](https://github.com/dholm/iridium-toolkit) |
| **Orbcomm** | 137–138 MHz VHF | IoT/M2M data messages, AIS supplemental | [gr-orbcomm](https://github.com/dholm/gr-orbcomm) |
| **QZSS (Michibiki) data broadcast** | 1278.75 MHz L6 | High-precision MADOCA-PPP augmentation, disaster alerts | [qzsl6tool](https://github.com/yoronneko/qzsl6tool) |

Each decoder will run as a subprocess with results displayed in a dedicated plugin panel inside the SDR Control tab. Offline re-analysis from saved IQ recordings is also planned.

#### UI / UX
- **Japanese UI** — translation files are already prepared; full JP mode coming in Phase 2
- **Observation log** — record, summarise, and export worked satellite passes
- **SDR Device Installation dialog** — USB VID/PID scan, guided driver install for RTL-SDR / HackRF on all platforms

#### Hardware
- Real-world Doppler tests with TS-2000, FT-817ND, etc. (IC-9100/IC-9700 confirmed in v0.1.27)
- WSJT-X / JS8Call frequency & mode sync

Contributions and feedback are welcome — open a [GitHub Issue](https://github.com/JF9SOM/fbsat59/issues) or submit a pull request.

---

## License

GPL-2.0-or-later (compatible with GPredict)

---

## Acknowledgements

- [GPredict](https://github.com/csete/gpredict) — Alexandru Csete OZ9AEC
- [Skyfield](https://rhodesmill.org/skyfield/) — Brandon Rhodes
- [Hamlib](https://hamlib.github.io/) — Hamlib Development Team
- [SATNOGS](https://satnogs.org/) — Libre Space Foundation
- [SoapySDR](https://github.com/pothosware/SoapySDR) — Pothosware
- [Direwolf](https://github.com/wb2osz/direwolf) — WB2OSZ (John Langner) — AX.25 / APRS / KISS software TNC
- [ft8_lib](https://github.com/kgoba/ft8_lib) — Kārlis Goba YL3JG — FT4/FT8 codec (C library, GPL-2.0)
- [pySSTV](https://github.com/dholm/pySSTV) — Dominik Heidler DL2DH — SSTV encoder/decoder
- [gr-satellites](https://github.com/daniestevez/gr-satellites) — Daniel Estévez EA4GPZ — amateur satellite telemetry decoders
