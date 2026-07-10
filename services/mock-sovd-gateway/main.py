"""
mock-sovd-gateway — SOVDpilot custom SOVD server
Replaces eclipse-opensovd/opensovd-gateway with a fully configurable mock.
SPDX-License-Identifier: Apache-2.0
"""

import random
import time
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="SOVDpilot Mock SOVD Gateway", version="1.0.0")

# ──────────────────────────────────────────────
# Component registry
# ──────────────────────────────────────────────
COMPONENTS = {
    "ecm": {
        "name": "Engine Control Module",
        "tags": ["powertrain", "critical"],
        "data": {
            "engine_speed":        lambda: round(random.uniform(700, 2500), 1),   # rpm
            "coolant_temperature": lambda: round(random.uniform(75, 105), 1),     # °C
            "oil_pressure":        lambda: round(random.uniform(2.5, 5.5), 2),    # bar
            "fuel_rate":           lambda: round(random.uniform(5.0, 35.0), 2),   # L/h
            "boost_pressure":      lambda: round(random.uniform(1.0, 2.8), 2),    # bar
            "egr_valve_position":  lambda: round(random.uniform(0, 100), 1),      # %
            "throttle_position":   lambda: round(random.uniform(0, 100), 1),      # %
        },
        "faults": [
            {"dtc": "P0087", "description": "Fuel Rail Pressure Too Low", "severity": "high"},
            {"dtc": "P0299", "description": "Turbocharger Underboost", "severity": "medium"},
            {"dtc": "P0401", "description": "EGR Insufficient Flow", "severity": "low"},
        ],
    },
    "tcm": {
        "name": "Transmission Control Module",
        "tags": ["powertrain"],
        "data": {
            "gear_selected":         lambda: random.randint(1, 9),
            "gear_actual":           lambda: random.randint(1, 9),
            "transmission_temp":     lambda: round(random.uniform(60, 110), 1),   # °C
            "input_shaft_speed":     lambda: round(random.uniform(0, 3000), 1),   # rpm
            "output_shaft_speed":    lambda: round(random.uniform(0, 1800), 1),   # rpm
            "torque_converter_slip": lambda: round(random.uniform(0, 50), 1),     # rpm
        },
        "faults": [
            {"dtc": "P0700", "description": "Transmission Control System Fault", "severity": "high"},
            {"dtc": "P0730", "description": "Incorrect Gear Ratio", "severity": "medium"},
        ],
    },
    "abs": {
        "name": "Anti-lock Braking System",
        "tags": ["safety", "braking", "critical"],
        "data": {
            "front_left_wheel_speed":  lambda: round(random.uniform(0, 120), 1),  # km/h
            "front_right_wheel_speed": lambda: round(random.uniform(0, 120), 1),
            "rear_left_wheel_speed":   lambda: round(random.uniform(0, 120), 1),
            "rear_right_wheel_speed":  lambda: round(random.uniform(0, 120), 1),
            "abs_active":              lambda: random.choice([True, False]),
            "brake_pressure":          lambda: round(random.uniform(0, 180), 1),  # bar
        },
        "faults": [
            {"dtc": "C0031", "description": "Left Front Wheel Speed Sensor Circuit", "severity": "high"},
            {"dtc": "C0034", "description": "Right Front Wheel Speed Sensor Circuit", "severity": "high"},
            {"dtc": "C0040", "description": "ABS Hydraulic Pump Motor Circuit", "severity": "critical"},
        ],
    },
    "ebc": {
        "name": "Electronic Brake Controller",
        "tags": ["safety", "braking", "critical"],
        "data": {
            "brake_demand":         lambda: round(random.uniform(0, 100), 1),     # %
            "brake_torque_actual":  lambda: round(random.uniform(0, 5000), 1),    # Nm
            "retarder_torque":      lambda: round(random.uniform(0, 2000), 1),    # Nm
            "brake_temp_axle1":     lambda: round(random.uniform(80, 350), 1),    # °C
            "brake_temp_axle2":     lambda: round(random.uniform(80, 350), 1),    # °C
            "ebs_system_pressure":  lambda: round(random.uniform(7.5, 12.5), 2),  # bar
        },
        "faults": [
            {"dtc": "C0267", "description": "Pump Motor Circuit Open", "severity": "critical"},
            {"dtc": "C0550", "description": "EBC Control Module Performance", "severity": "high"},
        ],
    },
    "aebs": {
        "name": "Advanced Emergency Braking System",
        "tags": ["safety", "adas", "critical"],
        "data": {
            "radar_target_distance":   lambda: round(random.uniform(5, 200), 1),  # m
            "radar_target_speed":      lambda: round(random.uniform(0, 120), 1),  # km/h
            "time_to_collision":       lambda: round(random.uniform(1.0, 10.0), 2), # s
            "aebs_state":              lambda: random.choice(["inactive", "warning", "partial_braking", "emergency_braking"]),
            "warning_active":          lambda: random.choice([True, False]),
            "system_available":        lambda: True,
        },
        "faults": [
            {"dtc": "U0126", "description": "Lost Communication With Steering Angle Sensor", "severity": "high"},
            {"dtc": "B1001", "description": "AEBS Radar Sensor Misalignment", "severity": "critical"},
            {"dtc": "B1002", "description": "AEBS Camera Obstruction Detected", "severity": "medium"},
        ],
    },
    "ldws": {
        "name": "Lane Departure Warning System",
        "tags": ["safety", "adas"],
        "data": {
            "lane_departure_left":    lambda: random.choice([True, False]),
            "lane_departure_right":   lambda: random.choice([True, False]),
            "left_lane_distance":     lambda: round(random.uniform(0.1, 1.5), 2),  # m
            "right_lane_distance":    lambda: round(random.uniform(0.1, 1.5), 2),  # m
            "camera_status":          lambda: random.choice(["ok", "degraded", "blocked"]),
            "vehicle_speed":          lambda: round(random.uniform(0, 120), 1),     # km/h
            "warning_suppressed":     lambda: random.choice([True, False]),
        },
        "faults": [
            {"dtc": "B2100", "description": "LDWS Camera Signal Lost", "severity": "high"},
            {"dtc": "B2101", "description": "LDWS Lane Marking Not Detected", "severity": "medium"},
        ],
    },
    "tpms": {
        "name": "Tyre Pressure Monitoring System",
        "tags": ["safety", "chassis"],
        "data": {
            "tyre_pressure_fl":   lambda: round(random.uniform(7.0, 9.5), 2),   # bar
            "tyre_pressure_fr":   lambda: round(random.uniform(7.0, 9.5), 2),
            "tyre_pressure_rl":   lambda: round(random.uniform(7.5, 10.0), 2),
            "tyre_pressure_rr":   lambda: round(random.uniform(7.5, 10.0), 2),
            "tyre_temp_fl":       lambda: round(random.uniform(20, 80), 1),     # °C
            "tyre_temp_fr":       lambda: round(random.uniform(20, 80), 1),
            "tyre_temp_rl":       lambda: round(random.uniform(20, 80), 1),
            "tyre_temp_rr":       lambda: round(random.uniform(20, 80), 1),
            "system_status":      lambda: "ok",
        },
        "faults": [
            {"dtc": "C1001", "description": "Front Left Tyre Pressure Low", "severity": "medium"},
            {"dtc": "C1002", "description": "Front Right Tyre Sensor Signal Lost", "severity": "high"},
        ],
    },
    "vecu": {
        "name": "Vehicle Electronic Control Unit",
        "tags": ["chassis", "critical"],
        "data": {
            "vehicle_speed":        lambda: round(random.uniform(0, 120), 1),    # km/h
            "cruise_control_active":lambda: random.choice([True, False]),
            "cruise_set_speed":     lambda: round(random.uniform(60, 100), 1),   # km/h
            "idle_shutdown_timer":  lambda: random.randint(0, 600),              # s
            "pto_engaged":          lambda: random.choice([True, False]),
            "battery_voltage":      lambda: round(random.uniform(24.0, 28.8), 2), # V
        },
        "faults": [
            {"dtc": "U0100", "description": "Lost Communication With ECM", "severity": "critical"},
            {"dtc": "P0579", "description": "Cruise Control Multi-Function Input Circuit Range", "severity": "low"},
        ],
    },
    "acm": {
        "name": "Aftertreatment Control Module",
        "tags": ["emissions", "powertrain"],
        "data": {
            "dpf_soot_load":         lambda: round(random.uniform(0, 100), 1),   # %
            "dpf_temp_inlet":        lambda: round(random.uniform(200, 650), 1), # °C
            "dpf_temp_outlet":       lambda: round(random.uniform(180, 620), 1), # °C
            "scr_efficiency":        lambda: round(random.uniform(85, 99), 1),   # %
            "def_level":             lambda: round(random.uniform(10, 100), 1),  # %
            "nox_upstream":          lambda: round(random.uniform(100, 800), 1), # ppm
            "nox_downstream":        lambda: round(random.uniform(5, 50), 1),    # ppm
            "regen_active":          lambda: random.choice([True, False]),
        },
        "faults": [
            {"dtc": "P2002", "description": "DPF Efficiency Below Threshold", "severity": "high"},
            {"dtc": "P203F", "description": "DEF Quality Poor", "severity": "high"},
            {"dtc": "P2201", "description": "NOx Sensor Circuit Range", "severity": "medium"},
        ],
    },
    "icm": {
        "name": "Instrument Control Module",
        "tags": ["cab", "display"],
        "data": {
            "odometer":             lambda: round(random.uniform(50000, 500000), 1), # km
            "trip_distance":        lambda: round(random.uniform(0, 1500), 1),       # km
            "fuel_level":           lambda: round(random.uniform(10, 100), 1),       # %
            "display_brightness":   lambda: random.randint(0, 100),                  # %
            "warning_lamp_active":  lambda: random.choice([True, False]),
            "mil_active":           lambda: random.choice([True, False]),
        },
        "faults": [
            {"dtc": "B1050", "description": "Instrument Cluster CAN Timeout", "severity": "medium"},
        ],
    },
    "bcm": {
        "name": "Body Control Module",
        "tags": ["cab", "body"],
        "data": {
            "door_driver_open":     lambda: random.choice([True, False]),
            "door_passenger_open":  lambda: random.choice([True, False]),
            "exterior_lights":      lambda: random.choice(["off", "position", "low_beam", "high_beam"]),
            "ambient_temp":         lambda: round(random.uniform(-10, 45), 1),   # °C
            "wiper_status":         lambda: random.choice(["off", "intermittent", "low", "high"]),
            "horn_active":          lambda: False,
        },
        "faults": [
            {"dtc": "B0082", "description": "Driver Door Ajar Switch Circuit", "severity": "low"},
            {"dtc": "B0090", "description": "Front Wiper Motor Circuit", "severity": "low"},
        ],
    },
}

# ──────────────────────────────────────────────
# Active fault store (inject ile tetiklenir)
# ──────────────────────────────────────────────
active_faults: dict[str, list] = {cid: [] for cid in COMPONENTS}


# ──────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────
def base_url(request_url: str, path: str) -> str:
    return f"http://localhost:7691/sovd/v1{path}"


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.get("/sovd/v1/components")
def list_components():
    items = []
    for cid, meta in COMPONENTS.items():
        items.append({
            "id": cid,
            "name": meta["name"],
            "href": f"http://localhost:7691/sovd/v1/components/{cid}",
            "tags": meta["tags"],
        })
    return {"items": items}


@app.get("/sovd/v1/components/{component_id}")
def get_component(component_id: str):
    if component_id not in COMPONENTS:
        raise HTTPException(status_code=404, detail=f"Component '{component_id}' not found")
    meta = COMPONENTS[component_id]
    return {
        "id": component_id,
        "name": meta["name"],
        "tags": meta["tags"],
        "links": {
            "data":   f"http://localhost:7691/sovd/v1/components/{component_id}/data",
            "faults": f"http://localhost:7691/sovd/v1/components/{component_id}/faults",
        },
    }


@app.get("/sovd/v1/components/{component_id}/data")
def get_component_data(component_id: str):
    if component_id not in COMPONENTS:
        raise HTTPException(status_code=404, detail=f"Component '{component_id}' not found")
    data_defs = COMPONENTS[component_id]["data"]
    items = []
    for key, fn in data_defs.items():
        items.append({
            "id": key,
            "href": f"http://localhost:7691/sovd/v1/components/{component_id}/data/{key}",
        })
    return {"items": items}


@app.get("/sovd/v1/components/{component_id}/data/{signal_id}")
def get_signal(component_id: str, signal_id: str):
    if component_id not in COMPONENTS:
        raise HTTPException(status_code=404, detail=f"Component '{component_id}' not found")
    data_defs = COMPONENTS[component_id]["data"]
    if signal_id not in data_defs:
        raise HTTPException(status_code=404, detail=f"Signal '{signal_id}' not found")
    return {
        "id": signal_id,
        "data": {"value": data_defs[signal_id]()},
        "timestamp": int(time.time() * 1000),
    }


@app.get("/sovd/v1/components/{component_id}/faults")
def get_faults(component_id: str):
    if component_id not in COMPONENTS:
        raise HTTPException(status_code=404, detail=f"Component '{component_id}' not found")
    return {"items": active_faults[component_id]}


@app.post("/sovd/v1/components/{component_id}/faults/inject")
def inject_fault(component_id: str, dtc: str):
    """Inject a DTC into a component (test/demo use)."""
    if component_id not in COMPONENTS:
        raise HTTPException(status_code=404, detail=f"Component '{component_id}' not found")
    # Find in fault template list
    templates = COMPONENTS[component_id]["faults"]
    match = next((f for f in templates if f["dtc"] == dtc), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"DTC '{dtc}' not defined for '{component_id}'")
    if match not in active_faults[component_id]:
        active_faults[component_id].append(match)
    return {"status": "injected", "fault": match}


@app.post("/sovd/v1/components/{component_id}/faults/clear")
def clear_faults(component_id: str):
    if component_id not in COMPONENTS:
        raise HTTPException(status_code=404, detail=f"Component '{component_id}' not found")
    active_faults[component_id] = []
    return {"status": "cleared", "component": component_id}


@app.get("/sovd/v1/version-info")
def version_info():
    return {
        "sovd_info": [{"version": "1.1", "base_uri": "http://localhost:7691/sovd"}],
        "implementation": "SOVDpilot Mock Gateway",
        "author": "Anadolu ISUZU / SOVDpilot",
    }


@app.get("/health")
def health():
    return {"status": "ok", "components": list(COMPONENTS.keys())}
