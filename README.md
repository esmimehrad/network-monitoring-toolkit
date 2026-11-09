# Network Monitoring Toolkit

A Python command-line toolkit that monitors network devices and services from a YAML inventory: TCP port checks, DNS resolution, optional ICMP ping, response-time measurement, SQLite history, and JSON/CSV reports.

> **Status:** Phase 1 of 14 — project scaffold and development environment. Features are added phase by phase; full documentation arrives in Phase 14.

## Quick start (Windows PowerShell)

```powershell
git clone https://github.com/esmimehrad/network-monitoring-toolkit.git
cd network-monitoring-toolkit
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
python main.py
pytest
```

## Responsible use

Only monitor devices and systems you own or have explicit permission to test.

## License

MIT — see [LICENSE](LICENSE).