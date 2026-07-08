# Session 01 Progress — SOVDpilot

**Date:** 2026-07-08
**Goal:** Local dev environment bootstrap on Windows + WSL2

---

## Environment

| Component | Version | Status |
|---|---|---|
| WSL2 distro | Ubuntu 26.04 LTS (Resolute) | OK |
| WSL2 kernel | 6.18.33.2-microsoft-standard-WSL2 | OK |
| Docker | 29.1.3 | OK |
| Docker Compose | v2.40.3-desktop.1 | OK |
| vcan kernel module | built-in to WSL2 kernel | OK |
| Python | 3.14 | OK |

---

## What Was Done

### 1. WSL2 + Docker Setup

- Confirmed WSL2 running with Ubuntu 26.04 LTS
- Enabled Docker Desktop WSL2 integration for Ubuntu distro
- Verified `docker` and `docker compose` accessible inside WSL2

### 2. GitHub Repository

- Cloned existing repo: `https://github.com/engbatuhanarslan/sovd-ai-diagnostic.git`
- Configured git identity (`user.name`, `user.email`)
- Set up SSH key (`ed25519`) and added to GitHub — no more password prompts
- Switched remote URL to SSH: `git@github.com:engbatuhanarslan/sovd-ai-diagnostic.git`
- Created initial directory structure and committed:

```
sovd-ai-diagnostics/
├── infra/
│   ├── ankaios/
│   └── compose/
│       └── docker-compose.dev.yml
├── services/
│   ├── can-simulator/
│   ├── sovd-server/
│   ├── kuksa-feeder/
│   │   └── sovd_feeder.py
│   └── diag-agent/
├── dashboard/
├── docs/
│   └── adr/
├── LICENSES/
├── LICENSE
└── README.md
```

### 3. vcan0 — Virtual CAN Interface

- `vcan` module confirmed available in WSL2 kernel (no custom kernel build needed)
- Brought up `vcan0` directly in WSL2:

```bash
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set up vcan0
```

- Smoke tested with `cansend` / `candump`:

```bash
# Terminal 1
candump vcan0

# Terminal 2
cansend vcan0 7DF#0201050000000000
# UDS "read coolant temperature" request frame
```

**Result:** CAN frame transmitted and received successfully on `vcan0`.

### 4. Kuksa Databroker

- Deployed via Docker Compose (`infra/compose/docker-compose.dev.yml`):

```yaml
services:
  kuksa-databroker:
    image: ghcr.io/eclipse-kuksa/kuksa-databroker:0.4.4
    container_name: kuksa-databroker
    ports:
      - "55555:55555"
    command: ["--insecure"]
```

- Installed Python client: `pip3 install kuksa-client`
- Smoke tested VSS signal write/read:

```python
from kuksa_client.grpc import VSSClient, Datapoint

with VSSClient("127.0.0.1", 55555) as client:
    client.set_current_values({
        "Vehicle.OBD.CoolantTemperature": Datapoint(95.0),
        "Vehicle.OBD.EngineSpeed": Datapoint(2500.0)
    })
    values = client.get_current_values([
        "Vehicle.OBD.CoolantTemperature",
        "Vehicle.OBD.EngineSpeed"
    ])
```

**Result:** `CoolantTemp: 95.0 C`, `EngineSpeed: 2500.0 RPM` — read back successfully.

> **Note:** `kuksa_client` API uses synchronous `with` context manager (not `async with`)
> and requires `Datapoint()` wrapper around values.

### 5. OpenSOVD Gateway

- Discovered official Docker image: `ghcr.io/eclipse-opensovd/opensovd-gateway`
- Source repo: `https://github.com/eclipse-opensovd/opensovd-core` (Rust, Apache-2.0)
- Implements ISO 17978-3:2026 SOVD standard, version 1.1
- Launched with `--mock` flag for local development:

```bash
docker run -d \
  --name opensovd-gateway \
  -p 7690:7690 \
  ghcr.io/eclipse-opensovd/opensovd-gateway --mock
```

- Explored SOVD REST API:

| Endpoint | Response |
|---|---|
| `GET /sovd/version-info` | SOVD v1.1, OpenSOVD v0.1.1 |
| `GET /sovd/v1/components` | `ecu` (Engine Control Unit), `gateway` (Vehicle Gateway) |
| `GET /sovd/v1/components/ecu/data` | voltage, temperature, sw.version, hw.version, ... |
| `GET /sovd/v1/components/ecu/data/voltage` | `12.6 V` |
| `GET /sovd/v1/components/ecu/data/temperature` | `85.0 C` |

### 6. SOVD to Kuksa Feeder (`sovd_feeder.py`)

- Written to `services/kuksa-feeder/sovd_feeder.py`
- Polls OpenSOVD gateway every 2 seconds
- Maps SOVD signals to VSS paths and feeds Kuksa Databroker

```
OpenSOVD /ecu/data/temperature  ->  Vehicle.OBD.CoolantTemperature
OpenSOVD /ecu/data/voltage      ->  Vehicle.OBD.ControlModuleVoltage
```

**Live output:**

```
SOVDpilot feeder starting...
Connected to Kuksa at 127.0.0.1:55555
  temperature  -> Vehicle.OBD.CoolantTemperature: 85.0
  voltage      -> Vehicle.OBD.ControlModuleVoltage: 12.6
```

**Result:** End-to-end data pipeline working — SOVD -> Feeder -> Kuksa.

---

## Current Stack Diagram

```
+---------------------------------------------+
|              WSL2 Ubuntu 26.04              |
|                                             |
|  vcan0 (virtual CAN interface)              |
|     |                                       |
|     +-- candump / cansend (smoke test only) |
|                                             |
|  +------------------+   HTTP poll (2s)      |
|  | OpenSOVD Gateway | <------------------+  |
|  |   :7690  (mock)  |                    |  |
|  +------------------+                    |  |
|                           +--------------+--+  |
|                           | sovd_feeder.py  |  |
|                           +--------------+--+  |
|                                          | gRPC   |
|  +------------------+                   |        |
|  | Kuksa Databroker | <-----------------+        |
|  |      :55555      |                            |
|  +------------------+                            |
+---------------------------------------------+
```

---

## Known Issues / Notes

- `vcan0` does not persist across WSL2 restarts — must re-run `modprobe` + `ip link`
  commands each session. Will be scripted as `infra/scripts/setup-vcan.sh`.
- OpenSOVD mock data is static (85.0 C, 12.6 V) — dynamic DTC simulation planned
  for the next session.
- Kuksa client requires `Datapoint()` wrapper; raw floats cause `AttributeError`.

---

## Next Session Goals

- Add DTC fault simulation to OpenSOVD mock (or use `classic-diagnostic-adapter`)
- Write first diag agent skeleton — Anthropic API + tool-calling (SOVD + Kuksa as tools)
- Add `vcan0` init script to repo (`infra/scripts/setup-vcan.sh`)
- Update `docker-compose.dev.yml` to include OpenSOVD gateway
- Commit everything and tag `v0.1.0-dev`
