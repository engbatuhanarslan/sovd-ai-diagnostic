# Session 02 Progress — SOVDpilot

**Date:** 2026-07-08
**Branch:** main
**Tag:** v0.1.0-dev

---

## Goals for This Session

- Add `infra/scripts/setup-vcan.sh` to the repository
- Update `docker-compose.dev.yml` to include OpenSOVD gateway and kuksa-feeder
- Write DTC fault simulation tool (`tools/inject-dtc/inject_dtc.py`)
- Write first diag agent skeleton with Anthropic/Groq API + tool-calling
- Commit everything and tag `v0.1.0-dev`

---

## What Was Accomplished

### 1. Repository Structure Cleanup

The correct directory layout was established under the repo root:

```
docker-compose.dev.yml
infra/scripts/setup-vcan.sh
services/diag-agent/diag_agent.py
tools/inject-dtc/inject_dtc.py
```

Files were accidentally created inside `infra/` during the session due to working
directory confusion (`infra$` prompt). The misplaced directories (`infra/infra/`,
`infra/services/`, `infra/tools/`) were removed and files were placed correctly.

### 2. `infra/scripts/setup-vcan.sh`

Shell script that:
- Loads the `vcan` kernel module via `modprobe`
- Creates and brings up the `vcan0` virtual CAN interface
- Performs a smoke test with `cansend` (if `can-utils` is installed)
- Skips gracefully if the interface already exists
- Works natively on WSL2 (kernel 6.18+) without privileged containers

### 3. `docker-compose.dev.yml` — Full Dev Stack

Updated to include all three services:

| Service | Image | Port |
|---|---|---|
| `kuksa-databroker` | `ghcr.io/eclipse-kuksa/kuksa-databroker:0.4.4` | 55555 |
| `opensovd-gateway` | `ghcr.io/eclipse-opensovd/opensovd-gateway:latest` | 7690 |
| `kuksa-feeder` | `python:3.12-slim` (mounts `services/kuksa-feeder/`) | — |

All services share a `sovdpilot-net` bridge network so container hostnames resolve
correctly (`sovdpilot-kuksa`, `opensovd-gateway`).

**Key fix:** `sovd_feeder.py` had hardcoded `127.0.0.1` for both SOVD and Kuksa
hosts. Updated to read `SOVD_BASE_URL`, `KUKSA_HOST`, `KUKSA_PORT` from environment
variables, which are injected by docker-compose.

### 4. OpenSOVD Mock API — Endpoint Discovery

Explored the OpenSOVD mock REST API to map all available endpoints:

```
GET /sovd/v1/components                                    → lists ecu, gateway
GET /sovd/v1/components/ecu                                → ECU metadata
GET /sovd/v1/components/ecu/data                           → lists voltage, temperature, ident data
GET /sovd/v1/components/ecu/data/voltage                   → {"data": {"value": 12.6}}
GET /sovd/v1/components/ecu/data/temperature               → {"data": {"value": 85.0}}
GET /sovd/v1/apps/engine_control                           → engine control app metadata
GET /sovd/v1/apps/engine_control/data/app.status           → {"data": {"value": "running"}}
GET /sovd/v1/apps/engine_control/data/fuel_injection.rate  → {"data": {"value": 2.5}}
```

**Key finding:** The mock topology does **not** expose a `faults` endpoint.
The `apps/engine_control` resource lives under `/sovd/v1/apps/`, not
`/sovd/v1/components/` — agent tool routing was updated accordingly.

### 5. Diag Agent Skeleton (`services/diag-agent/diag_agent.py`)

First working implementation of the LLM-based diagnostic agent:

**LLM backend:** Switched from Anthropic API to **Groq** (`llama-3.3-70b-versatile`)
due to Anthropic requiring a paid account. Groq provides a free tier with tool-calling
support via OpenAI-compatible API.

**Tools defined:**

| Tool | Backend | Description |
|---|---|---|
| `get_ecu_data` | SOVD REST | Reads `voltage`, `temperature`, `fuel_injection.rate`, `app.status` |
| `get_vss_signal` | Kuksa gRPC | Reads `Vehicle.OBD.CoolantTemperature`, `Vehicle.OBD.ControlModuleVoltage` |

**Agent loop:** Standard tool-calling loop — LLM decides which tools to call,
results are fed back, loop continues until `finish_reason == "stop"`.

**First successful end-to-end run:**

```
[agent] → get_ecu_data({'component': 'ecu', 'data_id': 'voltage'})
[agent] ← {"id": "voltage", "data": {"value": 12.6}}
[agent] → get_ecu_data({'component': 'ecu', 'data_id': 'temperature'})
[agent] ← {"id": "temperature", "data": {"value": 85.0}}
[agent] → get_vss_signal({'vss_path': 'Vehicle.OBD.CoolantTemperature'})
[agent] ← {"vss_path": "Vehicle.OBD.CoolantTemperature", "value": 85.0, ...}
[agent] → get_vss_signal({'vss_path': 'Vehicle.OBD.ControlModuleVoltage'})
[agent] ← {"vss_path": "Vehicle.OBD.ControlModuleVoltage", "value": 12.6, ...}
```

The agent successfully fetched live data from both SOVD and Kuksa and produced
a natural-language root-cause analysis for fault code P0420.

### 6. DTC Fault Injector (`tools/inject-dtc/inject_dtc.py`)

Tool for simulating DTC events during development. Writes a value to a Kuksa
VSS path which the agent polls. The `Vehicle.Diagnostics.ActiveFaultCode` custom
path was attempted but rejected by Kuksa (not in VSS 4.0 spec). `Vehicle.OBD.DTCList`
was also attempted but Kuksa's Python client 0.5.1 has a bug with array-type
Datapoint values. The poller currently uses `Vehicle.OBD.CoolantTemperature`
as a change-trigger workaround — a proper solution is tracked as a known issue.

### 7. Git Commit and Tag

```
commit a935a4f
feat: add diag agent skeleton, DTC injector, vcan setup script, dev compose
tag: v0.1.0-dev
```

---

## Issues Encountered and Resolved

| Issue | Root Cause | Fix |
|---|---|---|
| Container name conflict on `docker compose up` | Faz 1 containers still running | `docker rm -f <container>` before compose up |
| `pip install` failed system-wide | Ubuntu 26.04 PEP 668 externally managed | Created `~/sovdpilot-venv` virtualenv |
| `python` command not found | Ubuntu uses `python3` | Used `python3` everywhere |
| `sovd_feeder.py` `ModuleNotFoundError: requests` | Missing dependency in compose pip install | Added `requests` to compose command |
| Feeder Kuksa connection refused | Hardcoded `127.0.0.1` in feeder | Updated to read env vars |
| `Vehicle.Diagnostics.ActiveFaultCode` 404 | Non-standard VSS path | Switched to standard VSS paths |
| `Vehicle.OBD.DTCList` array Datapoint bug | kuksa-client 0.5.1 array handling bug | Workaround: use scalar VSS path as trigger |
| Groq model decommissioned | `llama-3.1-70b-versatile` deprecated | Updated to `llama-3.3-70b-versatile` |
| `apps/engine_control` 404 from agent | Agent used `/components/apps/...` path | Fixed routing: `apps/` prefix → `/sovd/v1/apps/` |

---

## Current Stack Status

```
sovdpilot-kuksa     UP   :55555  Kuksa Databroker 0.4.4
sovdpilot-opensovd  UP   :7690   OpenSOVD Gateway (mock mode)
sovdpilot-feeder    UP   —       Polls SOVD every 2s → pushes to Kuksa
```

VSS signals live in Kuksa:
- `Vehicle.OBD.CoolantTemperature` = 85.0 °C
- `Vehicle.OBD.ControlModuleVoltage` = 12.6 V

---

## Known Issues / Next Session

- [ ] `tools/inject-dtc/inject_dtc.py` — kuksa-client 0.5.1 array Datapoint bug;
      need a reliable DTC event trigger mechanism
- [ ] `apps/engine_control` path routing fix not yet committed
- [ ] No `faults` endpoint in OpenSOVD mock — investigate `classic-diagnostic-adapter`
      or mock topology customization for DTC simulation
- [ ] Groq API key is temporary/free tier — replace with Anthropic API key before
      hackathon demo (Anthropic tool-calling is more reliable for production)
- [ ] `docker-compose.dev.yml` healthchecks removed for simplicity — re-add before
      blueprint submission

---

## Next Session Goals (Session 03)

1. Fix DTC event injection — find a reliable trigger path in VSS 4.0
2. Investigate OpenSOVD `classic-diagnostic-adapter` for real DTC simulation
3. Wire the full demo loop: inject DTC → poller detects → agent runs → analysis printed
4. Begin `docs/ARCHITECTURE.md` and Mermaid diagram for README
5. Write `services/diag-agent/Dockerfile` so agent runs as a proper container
