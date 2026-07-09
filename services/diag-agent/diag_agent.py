#!/usr/bin/env python3
"""
services/diag-agent/diag_agent.py
SOVDpilot Diagnostic Agent — Ollama local LLM backend.
SPDX-License-Identifier: Apache-2.0
"""

import json, os, sys, time
from typing import Any
import httpx

try:
    from openai import OpenAI
except ImportError:
    sys.exit("openai SDK not installed. Run: pip install openai")

try:
    from kuksa_client.grpc import VSSClient, Datapoint
except ImportError:
    sys.exit("kuksa-client not installed.")

SOVD_BASE_URL  = os.getenv("SOVD_BASE_URL", "http://localhost:7690")
KUKSA_HOST     = os.getenv("KUKSA_HOST", "127.0.0.1")
KUKSA_PORT     = int(os.getenv("KUKSA_PORT", "55555"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
MODEL          = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
POLL_INTERVAL  = 5
TRIGGER_PATH   = "Vehicle.OBD.ThrottlePosition"
NORMAL_MAX     = 100.0  # >100 means encoded DTC

DTC_DESCRIPTIONS = {
    "P0420": "Catalyst System Efficiency Below Threshold (Bank 1)",
    "P0300": "Random/Multiple Cylinder Misfire Detected",
    "P0171": "System Too Lean (Bank 1)",
    "P0101": "Mass Air Flow Sensor Circuit Range/Performance",
    "P0301": "Cylinder 1 Misfire Detected",
}

def value_to_dtc(value: float) -> str:
    return f"P{int(value):04d}"  # 420.0 → P0420

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_ecu_data",
            "description": (
                "Read a data item from the vehicle ECU or engine control app via SOVD REST API. "
                "component='ecu': data_id options are 'voltage' (V) and 'temperature' (C). "
                "component='apps/engine_control': data_id options are 'app.status' and 'fuel_injection.rate' (L/h)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "component": {"type": "string"},
                    "data_id":   {"type": "string"},
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
                "Available: 'Vehicle.OBD.CoolantTemperature' (C), "
                "'Vehicle.OBD.ControlModuleVoltage' (V), "
                "'Vehicle.OBD.EngineSpeed' (RPM)."
            ),
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

Available SOVD data (use get_ecu_data):
- component='ecu', data_id='voltage'                             -> battery voltage (V)
- component='ecu', data_id='temperature'                         -> engine temperature (C)
- component='apps/engine_control', data_id='app.status'          -> ECU app status
- component='apps/engine_control', data_id='fuel_injection.rate' -> fuel injection rate (L/h)

Available VSS signals (use get_vss_signal):
- Vehicle.OBD.CoolantTemperature  (C)
- Vehicle.OBD.ControlModuleVoltage (V)
- Vehicle.OBD.EngineSpeed (RPM)

When given a fault event:
1. Fetch ALL available data points from SOVD and Kuksa.
2. Correlate values with the fault code and known thresholds.
3. Provide a concise root-cause analysis referencing specific values.
4. Suggest 3-5 actionable next steps for the technician.
"""

def sovd_get(path: str) -> Any:
    url = f"{SOVD_BASE_URL}{path}"
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        return {"error": str(exc)}

def kuksa_get(vss_path: str) -> Any:
    try:
        with VSSClient(KUKSA_HOST, KUKSA_PORT, ensure_startup_connection=False) as client:
            values = client.get_current_values([vss_path])
            dp = values.get(vss_path)
            if dp is None or dp.value is None:
                return {"vss_path": vss_path, "value": None}
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

def run_agent(fault_event: dict) -> str:
    # Ollama OpenAI-compat client — api_key dummy, zorunlu parametre
    client = OpenAI(
        base_url=OLLAMA_BASE_URL,
        api_key="ollama",
    )

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
    print(f"\n[agent] Analysing fault: {fault_event.get('dtc')} — {fault_event.get('description')}")
    print(f"[agent] Using model: {MODEL} via {OLLAMA_BASE_URL}")

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

        finish_reason = response.choices[0].finish_reason

        if finish_reason == "stop":
            return msg.content or "(no response)"
        if finish_reason != "tool_calls":
            return f"(unexpected finish_reason: {finish_reason})"

        for tc in msg.tool_calls:
            inputs = json.loads(tc.function.arguments)
            print(f"[agent] -> {tc.function.name}({inputs})")
            result_str = execute_tool(tc.function.name, inputs)
            print(f"[agent] <- {result_str[:100]}{'...' if len(result_str)>100 else ''}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str,
            })

def poll_for_faults() -> None:
    last_value: float | None = None
    print(f"[poller] Watching {TRIGGER_PATH} > {NORMAL_MAX} for fault trigger")
    print(f"[poller] LLM backend: {OLLAMA_BASE_URL} | model: {MODEL}")
    print("[poller] Run 'python3 tools/inject-dtc/inject_dtc.py --dtc <CODE>' to trigger. Example: --dtc P0300\n")

    while True:
        raw = kuksa_get(TRIGGER_PATH)
        value = raw.get("value")

        if value is not None and float(value) > NORMAL_MAX and value != last_value:
            last_value = value
            dtc_code = value_to_dtc(float(value))
            desc = DTC_DESCRIPTIONS.get(dtc_code, f"Fault code {dtc_code}")
            event = {"dtc": dtc_code, "description": desc, "encoded_value": value}
            print(f"[poller] Fault detected: {TRIGGER_PATH} = {value} → {dtc_code}")

            analysis = run_agent(event)
            print("\n" + "=" * 60)
            print("SOVDpilot Analysis:")
            print("=" * 60)
            print(analysis)
            print("=" * 60 + "\n")

            # Reset trigger
            with VSSClient(KUKSA_HOST, KUKSA_PORT) as client:
                client.set_current_values({TRIGGER_PATH: Datapoint(0.0)})
            last_value = None

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    # Ollama için API key gerekmez ama env kontrolü kaldırıldı
    try:
        poll_for_faults()
    except KeyboardInterrupt:
        print("\n[agent] Shutting down.")