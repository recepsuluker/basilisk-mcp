"""Ready-made session specs that mirror official Basilisk example scenarios."""

from __future__ import annotations

import copy
import math
from typing import Any

from .errors import ValidationError

EARTH_MU = 3.986004360e14  # [m^3/s^2] Basilisk createEarth()

_BASIC_ORBIT_A_KM = 7000.0  # [km]
_BASIC_ORBIT_PERIOD_S = 2.0 * math.pi * math.sqrt((_BASIC_ORBIT_A_KM * 1e3) ** 3 / EARTH_MU)  # [s]

TEMPLATES: dict[str, dict[str, Any]] = {
    "basic_orbit": {
        "description": (
            "Point-mass (optionally J2) Earth orbit propagation of a single spacecraft. Mirrors the LEO "
            "case of Basilisk's examples/scenarioBasicOrbit.py: a=7000 km, e=0.0001, i=33.3 deg, "
            "RAAN=48.2 deg, argp=347.8 deg, nu=85.3 deg, 10 s step."
        ),
        "basilisk_example": "examples/scenarioBasicOrbit.py",
        "suggested_duration_s": 0.75 * _BASIC_ORBIT_PERIOD_S,
        "spec": {
            "simulation": {"dt_s": 10.0, "fsw_dt_s": None, "integrator": "rk4"},
            "modules": {
                "sc": {"type": "spacecraft", "params": {
                    "orbit": {"semi_major_axis_km": _BASIC_ORBIT_A_KM, "eccentricity": 0.0001,
                              "inclination_deg": 33.3, "raan_deg": 48.2, "arg_periapsis_deg": 347.8,
                              "true_anomaly_deg": 85.3}}},
                "earth": {"type": "gravity", "params": {"central_body": "earth", "j2": False, "attach_to": ["sc"]}},
            },
            "connections": [],
            "recorders": {"states": {"source": "sc.state", "sample_dt_s": 10.0}},
        },
    },
    "attitude_feedback_torque": {
        "description": (
            "Inertial pointing with an MRP PD controller driving an ideal external torque. Mirrors "
            "Basilisk's examples/scenarioAttitudeFeedback.py (no disturbance torque)."
        ),
        "basilisk_example": "examples/scenarioAttitudeFeedback.py",
        "suggested_duration_s": 600.0,
        "spec": {
            "simulation": {"dt_s": 0.1, "fsw_dt_s": None, "integrator": "rk4"},
            "modules": {
                "sc": {"type": "spacecraft", "params": {
                    "orbit": {"semi_major_axis_km": 10000.0, "eccentricity": 0.01, "inclination_deg": 33.3,
                              "raan_deg": 48.2, "arg_periapsis_deg": 347.8, "true_anomaly_deg": 85.3},
                    "sigma_BN": [0.1, 0.2, -0.3], "omega_BN_B_radps": [0.001, -0.01, 0.03]}},
                "earth": {"type": "gravity", "params": {"central_body": "earth", "attach_to": ["sc"]}},
                "torquer": {"type": "ext_force_torque", "params": {"attach_to": "sc"}},
                "nav": {"type": "simple_nav", "params": {}},
                "guidance": {"type": "inertial3d", "params": {}},
                "att_error": {"type": "att_tracking_error", "params": {}},
                "control": {"type": "mrp_feedback", "params": {"K": 3.5, "P": 30.0, "Ki": -1.0,
                                                               "inertia_from": "sc"}},
            },
            "connections": [
                {"source": "sc.state", "target": "nav.state"},
                {"source": "nav.att", "target": "att_error.att"},
                {"source": "guidance.att_ref", "target": "att_error.ref"},
                {"source": "att_error.guid", "target": "control.guid"},
                {"source": "control.cmd_torque", "target": "torquer.cmd_torque"},
            ],
            "recorders": {
                "att_error_log": {"source": "att_error.guid", "sample_dt_s": 1.0},
                "torque_log": {"source": "control.cmd_torque", "sample_dt_s": 1.0},
            },
        },
    },
    "attitude_feedback_rw": {
        "description": (
            "Inertial pointing with an MRP PD controller and three Honeywell HR16 reaction wheels "
            "(initial speeds 100/200/300 rpm). Mirrors Basilisk's examples/scenarioAttitudeFeedbackRW.py."
        ),
        "basilisk_example": "examples/scenarioAttitudeFeedbackRW.py",
        "suggested_duration_s": 600.0,
        "spec": {
            "simulation": {"dt_s": 0.1, "fsw_dt_s": None, "integrator": "rk4"},
            "modules": {
                "sc": {"type": "spacecraft", "params": {
                    "mass_kg": 750.0, "inertia_kgm2": [900.0, 800.0, 600.0],
                    "orbit": {"semi_major_axis_km": 10000.0, "eccentricity": 0.01, "inclination_deg": 33.3,
                              "raan_deg": 48.2, "arg_periapsis_deg": 347.8, "true_anomaly_deg": 85.3},
                    "sigma_BN": [0.1, 0.2, -0.3], "omega_BN_B_radps": [0.001, -0.01, 0.03]}},
                "earth": {"type": "gravity", "params": {"central_body": "earth", "attach_to": ["sc"]}},
                "rw": {"type": "reaction_wheels", "params": {
                    "model": "Honeywell_HR16", "max_momentum_Nms": 50.0,
                    "spin_axes_B": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                    "initial_speeds_rpm": [100.0, 200.0, 300.0], "attach_to": "sc"}},
                "nav": {"type": "simple_nav", "params": {}},
                "guidance": {"type": "inertial3d", "params": {}},
                "att_error": {"type": "att_tracking_error", "params": {}},
                "control": {"type": "mrp_feedback", "params": {"K": 3.5, "P": 30.0, "Ki": -1.0,
                                                               "inertia_from": "sc"}},
                "rw_torque": {"type": "rw_motor_torque", "params": {}},
            },
            "connections": [
                {"source": "sc.state", "target": "nav.state"},
                {"source": "nav.att", "target": "att_error.att"},
                {"source": "guidance.att_ref", "target": "att_error.ref"},
                {"source": "att_error.guid", "target": "control.guid"},
                {"source": "rw.config", "target": "control.rw_params"},
                {"source": "rw.speeds", "target": "control.rw_speeds"},
                {"source": "control.cmd_torque", "target": "rw_torque.cmd_torque"},
                {"source": "rw.config", "target": "rw_torque.rw_params"},
                {"source": "rw_torque.motor_torque", "target": "rw.motor_torque"},
            ],
            "recorders": {
                "att_error_log": {"source": "att_error.guid", "sample_dt_s": 1.0},
                "rw_speed_log": {"source": "rw.speeds", "sample_dt_s": 1.0},
                "rw_torque_log": {"source": "rw_torque.motor_torque", "sample_dt_s": 1.0},
            },
        },
    },
    "nadir_pointing_rw": {
        "description": (
            "Nadir (Hill frame) pointing in a 500 km circular LEO with three HR16 wheels, starting from a "
            "tumbling attitude. Same control chain as attitude_feedback_rw with hill_point guidance."
        ),
        "basilisk_example": "examples/scenarioAttitudeGuidance.py (hillPoint) + scenarioAttitudeFeedbackRW.py",
        "suggested_duration_s": 900.0,
        "spec": {
            "simulation": {"dt_s": 0.1, "fsw_dt_s": None, "integrator": "rk4"},
            "modules": {
                "sc": {"type": "spacecraft", "params": {
                    "orbit": {"altitude_km": 500.0, "eccentricity": 0.0, "inclination_deg": 97.4},
                    "sigma_BN": [0.1, 0.2, -0.3], "omega_BN_B_radps": [0.001, -0.01, 0.03]}},
                "earth": {"type": "gravity", "params": {"central_body": "earth", "j2": True, "attach_to": ["sc"]}},
                "rw": {"type": "reaction_wheels", "params": {"attach_to": "sc"}},
                "nav": {"type": "simple_nav", "params": {}},
                "guidance": {"type": "hill_point", "params": {}},
                "att_error": {"type": "att_tracking_error", "params": {}},
                "control": {"type": "mrp_feedback", "params": {"inertia_from": "sc"}},
                "rw_torque": {"type": "rw_motor_torque", "params": {}},
            },
            "connections": [
                {"source": "sc.state", "target": "nav.state"},
                {"source": "nav.trans", "target": "guidance.trans"},
                {"source": "nav.att", "target": "att_error.att"},
                {"source": "guidance.att_ref", "target": "att_error.ref"},
                {"source": "att_error.guid", "target": "control.guid"},
                {"source": "rw.config", "target": "control.rw_params"},
                {"source": "rw.speeds", "target": "control.rw_speeds"},
                {"source": "control.cmd_torque", "target": "rw_torque.cmd_torque"},
                {"source": "rw.config", "target": "rw_torque.rw_params"},
                {"source": "rw_torque.motor_torque", "target": "rw.motor_torque"},
            ],
            "recorders": {
                "att_error_log": {"source": "att_error.guid", "sample_dt_s": 1.0},
                "rw_speed_log": {"source": "rw.speeds", "sample_dt_s": 1.0},
                "states": {"source": "sc.state", "sample_dt_s": 10.0},
            },
        },
    },
}


def list_templates() -> list[dict[str, Any]]:
    return [{"name": n, "description": t["description"], "basilisk_example": t["basilisk_example"],
             "suggested_duration_s": round(t["suggested_duration_s"], 3),
             "modules": {m: d["type"] for m, d in t["spec"]["modules"].items()},
             "recorders": sorted(t["spec"]["recorders"])}
            for n, t in TEMPLATES.items()]


def template_spec(name: str) -> dict[str, Any]:
    if name not in TEMPLATES:
        raise ValidationError(f"Unknown template {name!r}.", f"Available templates: {sorted(TEMPLATES)}")
    return copy.deepcopy(TEMPLATES[name]["spec"])
