# Session 03 Progress — SOVDpilot

**Date:** 2026-07-09
**Branch:** main

---

## Goals for This Session

- Install and evaluate a local LLM runtime (Ollama) as an alternative to Groq
- Switch `diag_agent.py` from Groq SDK to local Ollama backend
- Verify the full demo loop (inject DTC → poller → tool-calling → analysis) works
  with a local model
- Commit all changes and push to remote

---

## What Was Accomplished

### 1. Ollama Installation on WSL2 (Native Linux)

Ollama was installed natively on WSL2 Ubuntu using the official install script:

```bash
sudo apt-get install -y zstd   # required by newer Ollama installer
curl -fsSL https://ollama.com/install.sh | sh
```

**Note:** The installer failed on first attempt because `zstd` was missing.
Newer Ollama versions package the binary as `.tar.zst` for faster extraction.
This is expected on minimal WSL2 Ubuntu images.

### 2. Ollama Deployed as Docker Container

To align with the future Ankaios workload architecture, Ollama was also run as
a Docker container (in addition to the native install). This approach was chosen
deliberately — Ollama will eventually be defined as an Ankaios QM workload:

```bash
docker run -d \
  --name ollama \
  -p 11434:11434 \
  -v ollama_data:/root/.ollama \
  ollama/ollama
```

The `-v ollama_data:/root/.ollama` volume ensures downloaded models persist
across container restarts.

Model pulled:

```bash
docker exec -it ollama ollama pull llama3.1:8b
```

**Model size:** ~5 GB (Q4 quantized). Ollama serves the OpenAI-compatible REST
API on `http://localhost:11434/v1`.

### 3. Local LLM Evaluation — DoIP Question

A basic sanity check was run against the model before wiring it into the agent:

```bash
ollama run llama3.1:8b "What is the difference between CAN bus and DoIP in automotive diagnostics?"
```

**Findings:**
- General automotive concepts understood correctly
- DoIP definition partially wrong: model stated "Device Connectivity over IP"
  (correct: **Diagnostics over Internet Protocol**, ISO 13400)
- Model attributed DoIP to ASAM (incorrect — developed under ISO)
- No knowledge of Eclipse SDV ecosystem or SOVD standard

This confirms that a domain-specific system prompt (and eventually RAG with
ASAM SOVD spec / ISO 13400 documents) is necessary for production-quality
analysis. The 8B model is sufficient for demo purposes.

**Turkish language note:** `llama3.1:8b` performs poorly in Turkish.
`qwen2.5:7b` is a better alternative if Turkish output is needed.

### 4. Raspberry Pi 5 Feasibility Assessment

Evaluated running `llama3.1:8b` on the target hardware (Raspberry Pi 5, 8 GB):

| Metric | Expected |
|---|---|
| Model load time | 30–60 s |
| Inference speed | ~2–5 tok/s |
| Response latency | 30–120 s per analysis |
| RAM headroom | Marginal (OS ~1.5 GB + model ~6 GB) |

**Decision:** Pi 5 will run the SDV stack (OpenSOVD, Kuksa, Ankaios). LLM
inference stays on laptop/cloud (Groq API or local Ollama) during the hackathon
demo. This is a clean architectural separation and avoids demo latency risk.

For Pi 5 if local inference is ever needed: `qwen2.5:3b` or `llama3.2:3b`
(~2 GB, ~10–15 tok/s) are viable with reduced reasoning quality.

### 5. `diag_agent.py` — Switched to Ollama Backend

The Groq SDK dependency was replaced with the OpenAI-compatible client pointing
to the local Ollama endpoint. Changes were minimal by design:

**Removed:**
```python
from groq import Groq
client = Groq()
```

**Added:**
```python
from openai import OpenAI
client = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
```

New environment variables (with defaults):

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama API endpoint |
| `OLLAMA_MODEL` | `llama3.1:8b` | Model name |

`GROQ_API_KEY` guard removed — no API key required for local inference.

The tool definitions (`TOOLS`), system prompt (`SYSTEM_PROMPT`), tool execution
logic (`execute_tool`), and polling loop (`poll_for_faults`) are unchanged.

**Dependency change:**
```bash
pip install openai   # replaces groq SDK
```

### 6. Full Demo Loop — Verified with Local LLM

End-to-end demo loop executed successfully with Ollama backend:

```
inject_dtc.py (P0420 → 420.0) →
Kuksa ThrottlePosition →
poller detected →
agent triggered 6 tool calls →
llama3.1:8b produced root-cause analysis
```

**Tool call trace:**
```
[agent] -> get_ecu_data({'component': 'ecu', 'data_id': 'voltage'})
[agent] <- {"id": "voltage", "data": {"value": 12.6}}
[agent] -> get_ecu_data({'component': 'apps/engine_control', 'data_id': 'app.status'})
[agent] <- {"id": "app.status", "data": {"value": "running"}}
[agent] -> get_ecu_data({'component': 'apps/engine_control', 'data_id': 'fuel_injection.rate'})
[agent] <- {"id": "fuel_injection.rate", "data": {"value": 2.5}}
[agent] -> get_vss_signal({'vss_path': 'Vehicle.OBD.CoolantTemperature'})
[agent] <- {"vss_path": "Vehicle.OBD.CoolantTemperature", "value": 85.0, ...}
[agent] -> get_vss_signal({'vss_path': 'Vehicle.OBD.ControlModuleVoltage'})
[agent] <- {"vss_path": "Vehicle.OBD.ControlModuleVoltage", "value": 12.6, ...}
[agent] -> get_vss_signal({'vss_path': 'Vehicle.OBD.EngineSpeed'})
[agent] <- {"vss_path": "Vehicle.OBD.EngineSpeed", "value": null}
```

**Analysis quality note:** The 8B model flagged coolant temperature (85 °C) as
abnormal — this is within normal operating range. The 70B model (Groq) made
this distinction correctly. Root cause: insufficient domain knowledge in the
base model. Mitigation: strengthen system prompt with explicit threshold values,
and add RAG with SOVD/OBD reference data in a future session.

### 7. Groq vs Ollama Comparison

| | Groq (`llama-3.3-70b`) | Ollama (`llama3.1:8b`) |
|---|---|---|
| Inference speed | Fast (~3–5 s) | Slower (model/hw dependent) |
| Tool calling | ✅ | ✅ |
| Reasoning quality | High (70B) | Moderate (8B) |
| Internet required | Yes | No |
| Cost | Free tier / API key | Free, fully local |
| Hackathon risk | API downtime | None |

**Decision:** Keep both backends switchable via environment variables.
Groq for development quality checks, Ollama for offline/demo resilience.

---

## Files Changed

| File | Change |
|---|---|
| `services/diag-agent/diag_agent.py` | Groq SDK → OpenAI client (Ollama backend) |
| `docs/SESSION_03_PROGRESS.md` | This file |

---

## Issues Encountered and Resolved

| Issue | Root Cause | Fix |
|---|---|---|
| Ollama install failed | `zstd` not present on minimal WSL2 Ubuntu | `sudo apt-get install -y zstd` |
| `cp` failed for diag_agent.py | Output path not accessible from WSL | File applied manually |

---

## Known Issues (Carried Forward)

- [ ] DTC sourcing via `ThrottlePosition` is still a workaround — proper fix
      requires OpenSOVD `faults` endpoint or `classic-diagnostic-adapter`
- [ ] `Vehicle.OBD.DTCList` array Datapoint bug in `kuksa-client` 0.5.1 unresolved
- [ ] No `faults` endpoint in OpenSOVD mock topology
- [ ] Agent system prompt lacks explicit OBD threshold values — causes false
      positives in 8B model analysis (e.g., 85 °C coolant flagged as abnormal)
- [ ] `diag_agent.py` has no Dockerfile yet — needed for Ankaios workload packaging

---

## Next Session Goals (Session 04)

1. Strengthen system prompt with explicit OBD threshold values and DTC reference data
2. Write `services/diag-agent/Dockerfile` for container packaging
3. Begin `infra/ankaios/` manifest — define diag-agent and ollama as Ankaios workloads
4. Start `docs/ARCHITECTURE.md` with Mermaid diagram
5. Investigate OpenSOVD `classic-diagnostic-adapter` for proper DTC sourcing