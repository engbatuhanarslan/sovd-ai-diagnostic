#!/usr/bin/env python3
"""
services/diag-agent/diag_agent.py
SOVDpilot Diagnostic Agent — Ollama local LLM backend.
SPDX-License-Identifier: Apache-2.0
"""

import json, os, sys, time, threading
from typing import Any
import httpx

try:
    from openai import OpenAI
except ImportError:
    sys.exit("openai SDK not installed.")

try:
    from kuksa_client.grpc import VSSClient, Datapoint
except ImportError:
    sys.exit("kuksa-client not installed.")

# ── Config ────────────────────────────────────────────────────────────────────
SOVD_BASE_URL   = os.getenv("SOVD_BASE_URL", "http://localhost:7691")
KUKSA_HOST      = os.getenv("KUKSA_HOST", "127.0.0.1")
KUKSA_PORT      = int(os.getenv("KUKSA_PORT", "55555"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
MODEL           = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
POLL_INTERVAL   = 5

# Kuksa legacy trigger (inject_dtc.py uyumluluğu için korundu)
TRIGGER_PATH    = "Vehicle.OBD.ThrottlePosition"
NORMAL_MAX      = 100.0

DTC_DESCRIPTIONS = {
    "P0420": "Catalyst System Efficiency Below Threshold",
    "P0300": "Random/Multiple Cylinder Misfire Detected",
    "P0171": "System Too Lean (Bank 1)",
    "P0101": "Mass Air Flow Sensor Circuit Range/Performance",
    "P0301": "Cylinder 1 Misfire Detected",
    "P0087": "Fuel Rail Pressure Too Low",
    "P0299": "Turbocharger Underboost",
    "P0401": "EGR Insufficient Flow",
    "P0700": "Transmission Control System Fault",
    "P0730": "Incorrect Gear Ratio",
    "C0031": "Left Front Wheel Speed Sensor Circuit",
    "C0034": "Right Front Wheel Speed Sensor Circuit",
    "C0040": "ABS Hydraulic Pump Motor Circuit",
    "C0267": "Pump Motor Circuit Open",
    "C0550": "EBC Control Module Performance",
    "B1001": "AEBS Radar Sensor Misalignment",
    "B1002": "AEBS Camera Obstruction Detected",
    "B2100": "LDWS Camera Signal Lost",
    "B2101": "LDWS Lane Marking Not Detected",
    "C1001": "Front Left Tyre Pressure Low",
    "C1002": "Front Right Tyre Sensor Signal Lost",
    "U0100": "Lost Communication With ECM",
    "U0126": "Lost Communication With Steering Angle Sensor",
    "P2002": "DPF Efficiency Below Threshold",
    "P203F": "DEF Quality Poor",
    "P2201": "NOx Sensor Circuit Range",
    "B0082": "Driver Door Ajar Switch Circuit",
    "B0090": "Front Wiper Motor Circuit",
    "B1050": "Instrument Cluster CAN Timeout",
    "P0579": "Cruise Control Multi-Function Input Circuit Range",
}

# Tüm SOVD component'ları ve önemli sinyalleri
COMPONENT_SIGNALS = {
    "ecm":  ["engine_speed", "coolant_temperature", "oil_pressure", "fuel_rate", "boost_pressure"],
    "tcm":  ["gear_selected", "transmission_temp", "input_shaft_speed"],
    "abs":  ["front_left_wheel_speed", "front_right_wheel_speed", "abs_active", "brake_pressure"],
    "ebc":  ["brake_demand", "brake_torque_actual", "ebs_system_pressure", "brake_temp_axle1"],
    "aebs": ["radar_target_distance", "radar_target_speed", "time_to_collision", "aebs_state", "warning_active"],
    "ldws": ["lane_departure_left", "lane_departure_right", "left_lane_distance", "right_lane_distance", "camera_status"],
    "tpms": ["tyre_pressure_fl", "tyre_pressure_fr", "tyre_pressure_rl", "tyre_pressure_rr", "system_status"],
    "vecu": ["vehicle_speed", "battery_voltage", "cruise_control_active"],
    "acm":  ["dpf_soot_load", "dpf_temp_inlet", "scr_efficiency", "def_level", "nox_downstream"],
    "icm":  ["fuel_level", "warning_lamp_active", "mil_active"],
    "bcm":  ["ambient_temp", "exterior_lights"],
}

# ── Tool definitions ──────────────────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_sovd_signal",
            "description": (
                "Read a signal from any vehicle ECU via SOVD REST API. "
                "component options: ecm, tcm, abs, ebc, aebs, ldws, tpms, vecu, acm, icm, bcm. "
                "ecm signals: engine_speed (rpm), coolant_temperature (C), oil_pressure (bar), fuel_rate (L/h), boost_pressure (bar). "
                "aebs signals: radar_target_distance (m), radar_target_speed (km/h), time_to_collision (s), aebs_state, warning_active. "
                "ldws signals: lane_departure_left, lane_departure_right, left_lane_distance (m), right_lane_distance (m), camera_status. "
                "tpms signals: tyre_pressure_fl/fr/rl/rr (bar), tyre_temp_fl/fr/rl/rr (C). "
                "abs signals: front_left_wheel_speed, front_right_wheel_speed (km/h), abs_active, brake_pressure (bar). "
                "ebc signals: brake_demand (%), brake_torque_actual (Nm), ebs_system_pressure (bar), brake_temp_axle1 (C). "
                "vecu signals: vehicle_speed (km/h), battery_voltage (V), cruise_control_active. "
                "acm signals: dpf_soot_load (%), def_level (%), scr_efficiency (%), nox_downstream (ppm). "
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "component": {"type": "string"},
                    "signal_id": {"type": "string"},
                },
                "required": ["component", "signal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sovd_faults",
            "description": "Get active fault codes (DTCs) from any vehicle ECU component.",
            "parameters": {
                "type": "object",
                "properties": {
                    "component": {"type": "string"},
                },
                "required": ["component"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_vss_signal",
            "description": (
                "Read a VSS signal from Kuksa Databroker. "
                "Available: Vehicle.OBD.CoolantTemperature (C), "
                "Vehicle.OBD.ControlModuleVoltage (V), Vehicle.OBD.EngineSpeed (RPM)."
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
You have access to 11 ECU components via SOVD REST API: ecm, tcm, abs, ebc, aebs, ldws, tpms, vecu, acm, icm, bcm.

When given a fault event:
1. Identify which component(s) are relevant to the fault.
2. Fetch key signals from relevant components using get_sovd_signal.
3. Also check get_sovd_faults for related components.
4. Correlate all values with known thresholds and fault patterns.
5. Provide a concise root-cause analysis referencing specific values and units.
6. Suggest 3-5 actionable next steps for the technician.

Be specific — reference actual sensor values in your analysis.
"""

# ── HTTP / Kuksa helpers ──────────────────────────────────────────────────────
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
    if name == "get_sovd_signal":
        result = sovd_get(f"/sovd/v1/components/{inputs['component']}/data/{inputs['signal_id']}")
    elif name == "get_sovd_faults":
        result = sovd_get(f"/sovd/v1/components/{inputs['component']}/faults")
    elif name == "get_vss_signal":
        result = kuksa_get(inputs["vss_path"])
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result, default=str)

# ── Agent ─────────────────────────────────────────────────────────────────────
def run_agent(fault_event: dict) -> str:
    client = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Fault event detected:\n\n"
                f"```json\n{json.dumps(fault_event, indent=2)}\n```\n\n"
                "Fetch relevant vehicle data and provide a root-cause analysis."
            ),
        },
    ]
    print(f"\n[agent] Analysing: {fault_event.get('dtc')} — {fault_event.get('description')} (source: {fault_event.get('source', 'unknown')})")

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
            print(f"[agent] <- {result_str[:120]}{'...' if len(result_str) > 120 else ''}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str,
            })

# ── Seen fault tracker (component → set of DTCs) ─────────────────────────────
seen_faults: dict[str, set] = {c: set() for c in COMPONENT_SIGNALS}

def poll_sovd_faults() -> None:
    """Poll all SOVD components for new faults every POLL_INTERVAL seconds."""
    print(f"[sovd-poller] Watching {len(COMPONENT_SIGNALS)} components for faults")

    while True:
        for component in COMPONENT_SIGNALS:
            result = sovd_get(f"/sovd/v1/components/{component}/faults")
            items = result.get("items", [])
            for fault in items:
                dtc = fault.get("dtc")
                if dtc and dtc not in seen_faults[component]:
                    seen_faults[component].add(dtc)
                    desc = fault.get("description") or DTC_DESCRIPTIONS.get(dtc, f"Fault {dtc}")
                    event = {
                        "dtc": dtc,
                        "description": desc,
                        "severity": fault.get("severity", "unknown"),
                        "component": component,
                        "source": "sovd",
                    }
                    analysis = run_agent(event)
                    print("\n" + "=" * 60)
                    print(f"SOVDpilot Analysis [{component.upper()} / {dtc}]:")
                    print("=" * 60)
                    print(analysis)
                    print("=" * 60 + "\n")
        time.sleep(POLL_INTERVAL)

# ── Legacy Kuksa trigger (inject_dtc.py uyumluluğu) ──────────────────────────
def poll_kuksa_trigger() -> None:
    last_value = None
    print(f"[kuksa-poller] Watching {TRIGGER_PATH} > {NORMAL_MAX} for legacy DTC trigger")
    print(f"[kuksa-poller] LLM: {OLLAMA_BASE_URL} | model: {MODEL}")
    print("[kuksa-poller] Run 'python3 tools/inject-dtc/inject_dtc.py --dtc <CODE>' to trigger.\n")

    while True:
        raw = kuksa_get(TRIGGER_PATH)
        value = raw.get("value")
        if value is not None and float(value) > NORMAL_MAX and value != last_value:
            last_value = value
            dtc_code = f"P{int(float(value)):04d}"
            desc = DTC_DESCRIPTIONS.get(dtc_code, f"Fault code {dtc_code}")
            event = {"dtc": dtc_code, "description": desc, "source": "kuksa", "encoded_value": value}
            print(f"[kuksa-poller] Fault: {TRIGGER_PATH} = {value} → {dtc_code}")
            analysis = run_agent(event)
            print("\n" + "=" * 60)
            print(f"SOVDpilot Analysis [{dtc_code}]:")
            print("=" * 60)
            print(analysis)
            print("=" * 60 + "\n")
            with VSSClient(KUKSA_HOST, KUKSA_PORT) as client:
                client.set_current_values({TRIGGER_PATH: Datapoint(0.0)})
            last_value = None
        time.sleep(POLL_INTERVAL)

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    t1 = threading.Thread(target=poll_sovd_faults, daemon=True)
    t2 = threading.Thread(target=poll_kuksa_trigger, daemon=True)
    t1.start()
    t2.start()
    print("[agent] Both pollers running. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[agent] Shutting down.")