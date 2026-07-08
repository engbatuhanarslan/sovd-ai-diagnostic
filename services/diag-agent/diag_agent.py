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

VSS_DTC_FLAG = "Vehicle.OBD.DTCList"

# ── Tool definitions ──────────────────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_active_faults",
            "description": "Retrieve active DTC fault codes from the vehicle ECU via SOVD REST API.",
            "parameters": {
                "type": "object",
                "properties": {
                    "component": {
                        "type": "string",
                        "description": "SOVD component identifier (default: ecu)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ecu_data",
            "description": "Read a specific ECU data item from the SOVD REST API.",
            "parameters": {
                "type": "object",
                "properties": {
                    "component": {"type": "string"},
                    "data_id": {"type": "string"},
                },
                "required": ["component", "data_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_vss_signal",
            "description": "Read current value of a VSS signal from Kuksa Databroker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vss_path": {"type": "string"},
                },
                "required": ["vss_path"],
            },
        },
    },
]

SYSTEM_PROMPT = """\
You are SOVDpilot, an AI diagnostic copilot for Software Defined Vehicles (SDV).
You have access to vehicle diagnostic data via SOVD REST APIs and live VSS signals
via Kuksa Databroker.

When given a fault event:
1. Use get_active_faults to retrieve current DTCs from the ECU.
2. Use get_vss_signal to read relevant live sensor data (coolant temp, voltage, etc.).
3. Use get_ecu_data to fetch additional ECU parameters if needed.
4. Correlate the fault codes with the live signal values.
5. Provide a clear, concise root-cause analysis in plain language.
6. Suggest actionable next steps for the technician.

Be precise. Reference specific DTC codes, signal values, and thresholds.
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
    if name == "get_active_faults":
        component = inputs.get("component", "ecu")
        result = sovd_get(f"/sovd/v1/components/{component}/faults")
    elif name == "get_ecu_data":
        component = inputs.get("component", "ecu")
        data_id = inputs["data_id"]
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
                f"A new fault event has been detected:\n\n"
                f"```json\n{json.dumps(fault_event, indent=2)}\n```\n\n"
                "Please investigate and provide a root-cause analysis."
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
            print(f"[agent] → tool call: {tool_call.function.name}({inputs})")
            result_str = execute_tool(tool_call.function.name, inputs)
            print(f"[agent] ← result:    {result_str[:120]}{'...' if len(result_str) > 120 else ''}")
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result_str,
            })


# ── Poller ────────────────────────────────────────────────────────────────────

def poll_for_faults() -> None:
    last_seen: str | None = None
    print(f"[poller] Watching {VSS_DTC_FLAG} for fault events (poll interval: {POLL_INTERVAL}s)")
    print(f"[poller] Tip: run 'python3 tools/inject-dtc/inject_dtc.py' to trigger a fault.\n")

    while True:
        raw = kuksa_get(VSS_DTC_FLAG)
        current = raw.get("value")

        if current and current != last_seen:
            last_seen = current
            event = {"dtc": current, "description": f"Fault detected: {current}"}
            print(f"[poller] New fault event: {event}")
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
