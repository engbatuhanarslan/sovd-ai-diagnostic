#!/usr/bin/env python3
"""
tools/inject-dtc/inject_dtc.py

Injects a simulated DTC fault event into the local development environment
by writing a sentinel VSS signal to Kuksa Databroker. The diag agent watches
this signal and treats it as a fault trigger — identical to a real DTC
arriving via OpenSOVD.

Background:
  OpenSOVD --mock returns static fault data on GET /sovd/v1/components/ecu/faults.
  To simulate a *new* DTC event (edge trigger) we push a flag signal to Kuksa;
  the agent subscribes to this and fires its analysis flow.

Usage:
  python tools/inject-dtc/inject_dtc.py [--dtc P0101] [--severity warning]

SPDX-License-Identifier: Apache-2.0
"""

import argparse
import json
import sys
from datetime import datetime, timezone

try:
    from kuksa_client.grpc import VSSClient, Datapoint
except ImportError:
    sys.exit("kuksa-client not installed. Run: pip install kuksa-client")

# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 55555

# VSS path used as a DTC event flag (not in standard VSS spec; custom extension)
# The diag agent subscribes to this path and reads the JSON payload.
DTC_EVENT_PATH = "Vehicle.Diagnostics.ActiveFaultCode"

SEVERITY_MAP = {
    "info": 0,
    "warning": 1,
    "error": 2,
    "critical": 3,
}


def inject(dtc_code: str, severity: str, host: str, port: int) -> None:
    payload = json.dumps({
        "dtc": dtc_code,
        "severity": severity,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "description": f"Simulated fault: {dtc_code}",
    })

    print(f"[inject-dtc] Connecting to Kuksa Databroker at {host}:{port} ...")
    with VSSClient(host, port, ensure_startup_connection=True) as client:
        client.set_current_values({
            DTC_EVENT_PATH: Datapoint(payload),
        })
    print(f"[inject-dtc] DTC injected → {DTC_EVENT_PATH}")
    print(f"             Payload: {payload}")
    print()
    print("[inject-dtc] The diag agent should now pick up the fault event.")
    print(f"             Check: http://localhost:7690/sovd/v1/components/ecu/faults")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inject a simulated DTC fault event into Kuksa Databroker."
    )
    parser.add_argument("--dtc", default="P0101",
                        help="DTC code to inject (default: P0101)")
    parser.add_argument("--severity", default="warning",
                        choices=list(SEVERITY_MAP.keys()),
                        help="Fault severity level (default: warning)")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"Kuksa host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Kuksa gRPC port (default: {DEFAULT_PORT})")
    args = parser.parse_args()

    inject(
        dtc_code=args.dtc.upper(),
        severity=args.severity,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
