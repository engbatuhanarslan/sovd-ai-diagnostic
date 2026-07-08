#!/usr/bin/env python3
"""
services/diag-agent/diag_agent.py

SOVDpilot Diagnostic Agent — Faz 2 skeleton.

Connects to:
  - OpenSOVD Gateway (SOVD REST API, mock mode) for DTC and ECU data
  - Kuksa Databroker (gRPC) for live VSS signals

Uses Anthropic Claude with tool-calling:
  - get_active_faults    → GET /sovd/v1/components/ecu/faults
  - get_ecu_data         → GET /sovd/v1/components/ecu/data/{data_id}
  - get_vss_signal       → Kuksa gRPC current value

The agent polls Kuksa for a DTC event flag (injected by inject_dtc.py or a
real OpenSOVD fault notification), then runs a full analysis cycle.

Run:
  export ANTHROPIC_API_KEY=sk-ant-...
  python services/diag-agent/diag_agent.py

SPDX-License-Identifier: Apache-2.0
"""

import json
import os
import sys
import time
from typing import Any

import httpx

try:
    import anthropic
except ImportError:
    sys.exit("anthropic SDK not installed. Run: pip install anthropic")

try:
    from kuksa_client.grpc import VSSClient, Datapoint  # noqa: F401
except ImportError:
    sys.exit("kuksa-client not installed. Run: pip install kuksa-client")

# ── Configuration ─────────────────────────────────────────────────────────────
SOVD_BASE_URL = os.getenv("SOVD_BASE_URL", "http://localhost:7690")
KUKSA_HOST    = os.getenv("KUKSA_HOST", "127.0.0.1")
KUKSA_PORT    = int(os.getenv("KUKSA_PORT", "55555"))
MODEL         = "claude-sonnet-4-6"
POLL_INTERVAL = 5   # seconds between Kuksa polls

# VSS paths the agent monitors / queries
VSS_COOLANT_TEMP = "Vehicle.OBD.CoolantTemperature"
VSS_VOLTAGE      = "Vehicle.OBD.ControlModuleVoltage"
VSS_DTC_FLAG     = "Vehicle.Diagnostics.ActiveFaultCode"

# ── Tool definitions (Anthropic function-calling format) ──────────────────────
TOOLS: list[dict] = [
    {
        "name": "get_active_faults",
        "description": (
            "Retrieve the list of active DTC fault codes from the vehicle ECU "
            "via the SOVD REST API. Returns a list of fault objects including "
            "fault code, description, severity, and status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "description": "SOVD component identifier (default: 'ecu')",
                    "default": "ecu",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_ecu_data",
        "description": (
            "Read a specific ECU data item from the SOVD REST API. "
            "Use this to fetch runtime values like sensor readings or "
            "calibration parameters identified by a SOVD data_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "description": "SOVD component identifier (e.g. 'ecu')",
                },
                "data_id": {
                    "type": "string",
                    "description": "SOVD data resource identifier (e.g. 'coolant_temp', 'fuel_trim')",
                },
            },
            "required": ["component", "data_id"],
        },
    },
    {
        "name": "get_vss_signal",
        "description": (
            "Read the current value of a VSS (Vehicle Signal Specification) "
            "signal from the Kuksa Databroker. Use this for live sensor data "
            "such as coolant temperature or battery voltage."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "vss_path": {
                    "type": "string",
                    "description": (
                        "Dot-separated VSS path, e.g. "
                        "'Vehicle.OBD.CoolantTemperature'"
                    ),
                }
            },
            "required": ["vss_path"],
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
Do not speculate beyond the data available. If data is insufficient, say so.
"""

# ── Tool execution ─────────────────────────────────────────────────────────────

def sovd_get(path: str) -> Any:
    """HTTP GET against OpenSOVD gateway."""
    url = f"{SOVD_BASE_URL}{path}"
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        return {"error": str(exc), "url": url}


def kuksa_get(vss_path: str) -> Any:
    """Read a single VSS signal current value from Kuksa Databroker."""
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
    """Dispatch tool call to the correct backend and return result as JSON string."""
    if name == "get_active_faults":
        component = inputs.get("component", "ecu")
        result = sovd_get(f"/sovd/v1/components/{component}/faults")

    elif name == "get_ecu_data":
        component = inputs.get("component", "ecu")
        data_id   = inputs["data_id"]
        result = sovd_get(f"/sovd/v1/components/{component}/data/{data_id}")

    elif name == "get_vss_signal":
        result = kuksa_get(inputs["vss_path"])

    else:
        result = {"error": f"Unknown tool: {name}"}

    return json.dumps(result, default=str)


# ── Agent loop ────────────────────────────────────────────────────────────────

def run_agent(fault_event: dict) -> str:
    """
    Run one full agent cycle for a given fault event.
    Returns the final natural-language analysis string.
    """
    client = anthropic.Anthropic()

    user_message = (
        f"A new fault event has been detected:\n\n"
        f"```json\n{json.dumps(fault_event, indent=2)}\n```\n\n"
        "Please investigate this fault by querying the vehicle systems and "
        "provide a root-cause analysis with recommended actions."
    )

    messages: list[dict] = [{"role": "user", "content": user_message}]

    print(f"\n[agent] Starting analysis for fault: {fault_event.get('dtc', 'unknown')}")

    # Agentic tool-calling loop
    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Append assistant response to history
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Extract final text response
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return "(no text response)"

        if response.stop_reason != "tool_use":
            return f"(unexpected stop_reason: {response.stop_reason})"

        # Execute all tool calls in this turn
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"[agent] → tool call: {block.name}({json.dumps(block.input)})")
            result_str = execute_tool(block.name, block.input)
            print(f"[agent] ← result:    {result_str[:120]}{'...' if len(result_str) > 120 else ''}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
            })

        # Feed results back into the conversation
        messages.append({"role": "user", "content": tool_results})


# ── Event listener ────────────────────────────────────────────────────────────

def poll_for_faults() -> None:
    """
    Poll Kuksa for the DTC event flag written by inject_dtc.py.
    When a new event is detected, trigger the agent analysis loop.
    """
    last_seen_dtc: str | None = None
    print(f"[poller] Watching {VSS_DTC_FLAG} for fault events (poll interval: {POLL_INTERVAL}s)")
    print(f"[poller] Tip: run 'python tools/inject-dtc/inject_dtc.py' to trigger a fault.\n")

    while True:
        raw = kuksa_get(VSS_DTC_FLAG)
        current_value = raw.get("value")

        if current_value and current_value != last_seen_dtc:
            last_seen_dtc = current_value
            try:
                event = json.loads(current_value)
            except json.JSONDecodeError:
                event = {"raw": current_value}

            print(f"[poller] New fault event detected: {event}")
            analysis = run_agent(event)
            print("\n" + "=" * 60)
            print("SOVDpilot Analysis:")
            print("=" * 60)
            print(analysis)
            print("=" * 60 + "\n")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ANTHROPIC_API_KEY environment variable not set.")

    try:
        poll_for_faults()
    except KeyboardInterrupt:
        print("\n[agent] Shutting down.")
