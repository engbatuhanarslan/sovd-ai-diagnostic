#!/usr/bin/env python3
"""
tools/inject-dtc/inject_dtc.py
Encodes DTC code as a numeric value in Vehicle.OBD.ThrottlePosition.
P0420 → 420.0, P0300 → 300.0, P0171 → 171.0
SPDX-License-Identifier: Apache-2.0
"""
import argparse, sys
try:
    from kuksa_client.grpc import VSSClient, Datapoint
except ImportError:
    sys.exit("kuksa-client not installed.")

TRIGGER_PATH = "Vehicle.OBD.ThrottlePosition"

DTC_DESCRIPTIONS = {
    "P0420": "Catalyst System Efficiency Below Threshold (Bank 1)",
    "P0300": "Random/Multiple Cylinder Misfire Detected",
    "P0171": "System Too Lean (Bank 1)",
    "P0101": "Mass Air Flow Sensor Circuit Range/Performance",
    "P0301": "Cylinder 1 Misfire Detected",
}

def dtc_to_value(dtc: str) -> float:
    return float(dtc[1:])  # P0420 → 420.0

def inject(dtc_code: str, host: str, port: int) -> None:
    value = dtc_to_value(dtc_code)
    desc = DTC_DESCRIPTIONS.get(dtc_code, f"Fault code {dtc_code}")
    print(f"[inject-dtc] DTC     : {dtc_code} — {desc}")
    print(f"[inject-dtc] Encoded : {TRIGGER_PATH} = {value}")
    with VSSClient(host, port, ensure_startup_connection=True) as client:
        client.set_current_values({TRIGGER_PATH: Datapoint(value)})
    print(f"[inject-dtc] Done. Agent triggers within 5 seconds.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dtc", default="P0420")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=55555)
    args = parser.parse_args()
    inject(args.dtc.upper(), args.host, args.port)

if __name__ == "__main__":
    main()
