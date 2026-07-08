"""
SOVDpilot — SOVD to Kuksa Feeder (dev/mock)
Polls OpenSOVD gateway and feeds VSS signals into Kuksa Databroker.
"""
import os
import time
import requests
from kuksa_client.grpc import VSSClient, Datapoint

SOVD_BASE  = os.getenv("SOVD_BASE_URL", "http://127.0.0.1:7690") + "/sovd/v1/components/ecu/data"
KUKSA_HOST = os.getenv("KUKSA_HOST", "127.0.0.1")
KUKSA_PORT = int(os.getenv("KUKSA_PORT", "55555"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "2"))

SOVD_TO_VSS = {
    "temperature": "Vehicle.OBD.CoolantTemperature",
    "voltage":     "Vehicle.OBD.ControlModuleVoltage",
}

def fetch_sovd(signal_id: str) -> float | None:
    try:
        r = requests.get(f"{SOVD_BASE}/{signal_id}", timeout=3)
        r.raise_for_status()
        return r.json()["data"]["value"]
    except Exception as e:
        print(f"[SOVD] Error fetching {signal_id}: {e}")
        return None

def main():
    print(f"SOVDpilot feeder starting... KUKSA={KUKSA_HOST}:{KUKSA_PORT} SOVD={SOVD_BASE}")
    with VSSClient(KUKSA_HOST, KUKSA_PORT) as client:
        print(f"Connected to Kuksa at {KUKSA_HOST}:{KUKSA_PORT}")
        while True:
            updates = {}
            for sovd_id, vss_path in SOVD_TO_VSS.items():
                value = fetch_sovd(sovd_id)
                if value is not None:
                    updates[vss_path] = Datapoint(value)
                    print(f"  {sovd_id:12} -> {vss_path}: {value}")
            if updates:
                client.set_current_values(updates)
            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
