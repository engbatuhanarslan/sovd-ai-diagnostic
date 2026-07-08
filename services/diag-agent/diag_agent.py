#!/usr/bin/env python3
"""
services/diag-agent/diag_agent.py
SOVDpilot Diagnostic Agent — Groq backend.
SPDX-License-Identifier: Apache-2.0
"""

import json
import os
import sys
import time
from typing import Any

import httpx

try:
    from groq import Groq
except ImportError:
    sys.exit("groq SDK not installed. Run: pip install groq")

try:
    from kuksa_client.grpc import VSSClient, Datapoint  # noqa: F401
except ImportError:
    sys.exit("kuksa-client not installed. Run: pip install kuksa-client")

# ── Configuration ─────────────────────────────────────────────────────────────
SOVD_BASE_URL = os.getenv("SOVD_BASE_URL", "http://localhost:7690")
KUKSA_HOST    = os.getenv("KUKSA_HOST", "127.0.0.1")
KUKSA_PORT    = int(os.getenv("KUKSA_PORT", "55555"))
MODEL         = "llama-3.3-70b-versatile"
POLL_INTERVAL = 5

VSS_DTC_FLAG = "Vehicle.OBD.CoolantTemperature"  # poll trigger için

# ── Bilinen SOVD endpoint'leri (mock'tan keşfedildi) ─────────────────────────
# GET /sovd/v1/components/ecu/data/voltage        → {"data": {"value": 12.6}}
# GET /sovd/v1/components/ecu/data/temperature    → {"data": {"value": 85.0}}
# GET /sovd/v1/apps/engine_control/data/app.status       → {"data": {"value": "running"}}
# GET /sovd/v1/apps/engine_control/data/fuel_injection.rate → {"data": {"value": 2.5}}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_ecu_data",
            "description": (
                "Read a data item from the vehicle ECU or engine control app via SOVD REST API. "
                "Available data_ids for component='ecu': 'voltage' (battery voltage V), "
                "'temperature' (engine temp °C). "
                "Available data_ids for component='apps/engine_control': "
                "'app.status' (running/stopped), 'fuel_injection.rate' (L/h)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "component": {
                        "type": "string",
                        "description": "SOVD component path: 'ecu' or 'apps/engine_control'",
                    },
                    "data_id": {
                        "type": "string",
                        "description": "Data identifier, e.g. 'voltage', 'temperature', 'app.status', 'fuel_injection.rate'",
                    },
                },
                "required": ["component", "data_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_vss_signal",
            "description": (
                "Read current value of a VSS signal from Kuksa Databroker. "
                "Available signals: "
                "'Vehicle.OBD.CoolantTemperature' (°C), "
                "'Vehicle.OBD.ControlModuleVoltage' (V)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vss_path": {
                        "type": "string",
                        "description": "VSS dot-separated path",
                    },
                },
                "required": ["vss_path"],
            },
        },
    },
]

SYSTEM_PROMPT = """\
You are SOVDpilot, an AI diagnostic copilot for Software Defined Vehicles (SDV).
You have access to live vehicle data via SOVD REST APIs and Kuksa Databroker.

Available SOVD data:
- component='ecu', data_id='voltage'           → battery voltage (V)
- component='ecu', data_id='temperature'       → engine temperature (°C)
- component='apps/engine_control', data_id='app.status'          → ECU app status
- component='apps/engine_control', data_id='fuel_injection.rate' → fuel injection rate (L/h)

Available VSS signals:
- Vehicle.OBD.CoolantTemperature  (°C)
- Vehicle.OBD.ControlModuleVoltage (V)

When given a fault event:
1. Fetch all available SOVD data points (voltage, temperature, fuel injection rate, app status).
2. Fetch VSS signals from Kuksa.
3. Correlate values with the fault code.
4. Provide a root-cause analysis with specific values and thresholds.
5. Suggest actionable next steps for the technician.
"""

# ── Tool execution ─────────────────────────────────────────────────────────────

def sovd_get(path: str) -> Any:
    url = f"{SOVD_BASE_URL}{path}"
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        return {"error": str(exc), "url": url}


def kuksa_get(vss_path: str) -> Any:
    try:
        with VSSClient(KUKSA_HOST, KUKSA_PORT, ensure_startup_connection=False) as client:
            values = client.get_current_values([vss_path])
            dp = values.get(vss_path)
            if dp is None or dp.value is None:
                return {"vss_path": vss_path, "value": None, "status": "not_found"}
            return {"vss_path": vss_path, "value": dp.value, "timestamp": str(dp.timestamp)}
    except Exception as exc:
        return {"vss_path": vss_path, "error": str(exc)}


def execute_tool(name: str, inputs: dict) -> str:
    if name == "get_ecu_data":
        component = inputs["component"]
        data_id   = inputs["data_id"]
        if component.startswith("apps/"):
            result = sovd_get(f"/sovd/v1/{component}/data/{data_id}")
        else:
            result = sovd_get(f"/sovd/v1/components/{component}/data/{data_id}")
    elif name == "get_vss_signal":
        result = kuksa_get(inputs["vss_path"])
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result, default=str)


# ── Agent loop ────────────────────────────────────────────────────────────────

def run_agent(fault_event: dict) -> str:
    client = Groq()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Fault event detected:\n\n"
                f"```json\n{json.dumps(fault_event, indent=2)}\n```\n\n"
                "Fetch all available vehicle data and provide a root-cause analysis."
            ),
        },
    ]

    print(f"\n[agent] Starting analysis for fault: {fault_event.get('dtc', 'unknown')}")

    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=1024,
        )

        msg = response.choices[0].message
        messages.append(msg)

        if response.choices[0].finish_reason == "stop":
            return msg.content or "(no response)"

        if response.choices[0].finish_reason != "tool_calls":
            return f"(unexpected finish_reason: {response.choices[0].finish_reason})"

        for tool_call in msg.tool_calls:
            inputs = json.loads(tool_call.function.arguments)
            print(f"[agent] → {tool_call.function.name}({inputs})")
            result_str = execute_tool(tool_call.function.name, inputs)
            print(f"[agent] ← {result_str[:100]}{'...' if len(result_str) > 100 else ''}")
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result_str,
            })


# ── Poller ────────────────────────────────────────────────────────────────────

def poll_for_faults() -> None:
    last_seen: Any = None
    print(f"[poller] Watching {VSS_DTC_FLAG} (poll interval: {POLL_INTERVAL}s)")
    print("[poller] Tip: run 'python3 tools/inject-dtc/inject_dtc.py' to trigger.\n")

    while True:
        raw = kuksa_get(VSS_DTC_FLAG)
        current = raw.get("value")
        if current and current != last_seen:
            last_seen = current
            event = {"dtc": "P0420", "trigger_value": current,
                     "description": "Catalyst System Efficiency Below Threshold (Bank 1)"}
            print(f"[poller] Trigger detected: {VSS_DTC_FLAG}={current}")
            analysis = run_agent(event)
            print("\n" + "=" * 60)
            print("SOVDpilot Analysis:")
            print("=" * 60)
            print(analysis)
            print("=" * 60 + "\n")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    if not os.getenv("GROQ_API_KEY"):
        sys.exit("GROQ_API_KEY environment variable not set.")
    try:
        poll_for_faults()
    except KeyboardInterrupt:
        print("\n[agent] Shutting down.")
