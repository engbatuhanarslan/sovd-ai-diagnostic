#!/usr/bin/env python3
"""
tools/inject-dtc/inject_dtc.py
Injects a simulated DTC fault event by writing to a standard VSS path.
Uses Vehicle.OBD.DTC which exists in VSS 4.0 spec.
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

# VSS 4.0'da mevcut olan path
DTC_PATH = "Vehicle.OBD.DTCList"

def inject(dtc_code: str, host: str, port: int) -> None:
    print(f"[inject-dtc] Connecting to Kuksa at {host}:{port} ...")
    with VSSClient(host, port, ensure_startup_connection=True) as client:
        client.set_current_values({
            DTC_PATH: Datapoint([dtc_code]),
        })
    print(f"[inject-dtc] DTC injected → {DTC_PATH} = [{dtc_code}]")
    print(f"[inject-dtc] Agent should pick this up within 5 seconds.")

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dtc", default="P0420")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    inject(args.dtc.upper(), args.host, args.port)

if __name__ == "__main__":
    main()
