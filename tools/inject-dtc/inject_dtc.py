#!/usr/bin/env python3
"""
tools/inject-dtc/inject_dtc.py
Injects a simulated DTC event by writing a sentinel value to a VSS path.
Strategy: Vehicle.OBD.ThrottlePosition = 255.0 signals a fault condition.
The diag agent polls this path and triggers analysis when value > 200.
SPDX-License-Identifier: Apache-2.0
"""
import argparse
import sys

try:
    from kuksa_client.grpc import VSSClient, Datapoint
except ImportError:
    sys.exit("kuksa-client not installed.")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 55555
TRIGGER_PATH  = "Vehicle.OBD.ThrottlePosition"
TRIGGER_VALUE = 255.0  # sentinel — outside normal 0-100 range

DTC_DESCRIPTIONS = {
    "P0420": "Catalyst System Efficiency Below Threshold (Bank 1)",
    "P0101": "Mass Air Flow Sensor Circuit Range/Performance",
    "P0300": "Random/Multiple Cylinder Misfire Detected",
    "P0171": "System Too Lean (Bank 1)",
    "P0301": "Cylinder 1 Misfire Detected",
}

def inject(dtc_code: str, host: str, port: int) -> None:
    desc = DTC_DESCRIPTIONS.get(dtc_code, f"Fault code {dtc_code}")
    print(f"[inject-dtc] DTC      : {dtc_code} — {desc}")
    print(f"[inject-dtc] Trigger  : {TRIGGER_PATH} = {TRIGGER_VALUE}")
    print(f"[inject-dtc] Kuksa    : {host}:{port}")

    with VSSClient(host, port, ensure_startup_connection=True) as client:
        # Write the DTC code into a readable path as well
        client.set_current_values({
            TRIGGER_PATH: Datapoint(TRIGGER_VALUE),
            "Vehicle.OBD.EngineSpeed": Datapoint(float(hash(dtc_code) % 4000 + 500)),
        })

    print(f"[inject-dtc] Injected. Agent should trigger within 5 seconds.")

def main() -> None:
    parser = argparse.ArgumentParser(description="Inject a simulated DTC fault event.")
    parser.add_argument("--dtc", default="P0420", help="DTC code (default: P0420)")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    inject(args.dtc.upper(), args.host, args.port)

if __name__ == "__main__":
    main()
