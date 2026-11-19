"""Generate a standalone, human-readable Basilisk scenario script from a validated spec.

Security property: the only values written into the script are

* numbers and booleans emitted through ``repr(float(...))`` / ``repr(bool)``,
* enum strings taken from fixed tables in :mod:`basilisk_mcp.registry`,
* module/recorder names that match ``^[a-z][a-z0-9_]{0,31}$``.

No free-form client text reaches the generated code. The result is re-parsed with
:func:`ast.parse` before it is ever executed.

The structure deliberately mirrors the official Basilisk example scenarios
(``scenarioBasicOrbit.py``, ``scenarioAttitudeFeedbackRW.py``) so that results are
numerically identical and the exported script is familiar to Basilisk users.
"""

from __future__ import annotations

import ast
import math
from typing import Any

from . import __version__
from .errors import ValidationError
from .registry import (
    INTEGRATORS,
    J2_MODELS,
    MESSAGE_TYPES,
    MODULE_TYPES,
    NAME_RE,
    RW_MODELS,
    check_name,
)
from .spec import gravity_of, task_dt, wheel_count

IN_ATTR = {
    ("simple_nav", "state"): "scStateInMsg",
    ("ext_force_torque", "cmd_torque"): "cmdTorqueInMsg",
    ("reaction_wheels", "motor_torque"): "rwMotorCmdInMsg",
    ("hill_point", "trans"): "transNavInMsg",
    ("att_tracking_error", "att"): "attNavInMsg",
    ("att_tracking_error", "ref"): "attRefInMsg",
    ("mrp_feedback", "guid"): "guidInMsg",
    ("mrp_feedback", "rw_params"): "rwParamsInMsg",
    ("mrp_feedback", "rw_speeds"): "rwSpeedsInMsg",
    ("rw_motor_torque", "cmd_torque"): "vehControlInMsg",
    ("rw_motor_torque", "rw_params"): "rwParamsInMsg",
}

OUT_ATTR = {
    ("spacecraft", "state"): "scStateOutMsg",
    ("simple_nav", "att"): "attOutMsg",
    ("simple_nav", "trans"): "transOutMsg",
    ("reaction_wheels", "speeds"): "rwSpeedOutMsg",
    ("reaction_wheels", "config"): None,  # standalone message from rwFactory.getConfigMessage()
    ("inertial3d", "att_ref"): "attRefOutMsg",
    ("hill_point", "att_ref"): "attRefOutMsg",
    ("att_tracking_error", "guid"): "attGuidOutMsg",
    ("mrp_feedback", "cmd_torque"): "cmdTorqueOutMsg",
    ("rw_motor_torque", "motor_torque"): "rwMotorTorqueOutMsg",
}

GRAVITY_FACTORY = {
    "earth": "createEarth",
    "moon": "createMoon",
    "mars": "createMars",
    "sun": "createSun",
    "venus": "createVenus",
    "jupiter": "createJupiter",
}

SIM_IMPORTS = {
    "spacecraft": "spacecraft",
    "ext_force_torque": "extForceTorque",
    "simple_nav": "simpleNav",
    "reaction_wheels": "reactionWheelStateEffector",
}
FSW_IMPORTS = {
    "inertial3d": "inertial3D",
    "hill_point": "hillPoint",
    "att_tracking_error": "attTrackingError",
    "mrp_feedback": "mrpFeedback",
    "rw_motor_torque": "rwMotorTorque",
}

TASK = {"dynamics": "dynamicsTask", "fsw": "fswTask"}


# --------------------------------------------------------------------------------------
# Literal emitters: the only way values enter the script.
# --------------------------------------------------------------------------------------


def num(x: Any) -> str:
    if isinstance(x, bool):
        raise ValidationError("boolean where a number was expected")
    return repr(float(x))


def col(v: list[float]) -> str:
    """Basilisk column-vector literal: [[x], [y], [z]]."""
    return "[" + ", ".join(f"[{num(x)}]" for x in v) + "]"


def row(v: list[float]) -> str:
    return "[" + ", ".join(num(x) for x in v) + "]"


def mat(m: list[list[float]]) -> str:
    return "[" + ", ".join(row(r) for r in m) + "]"


def enum(value: str, allowed) -> str:
    if value not in allowed:
        raise ValidationError(f"value {value!r} not allowed")
    return repr(value)


def var(name: str) -> str:
    check_name(name)
    return f"m_{name}"


def rec_var(name: str) -> str:
    check_name(name)
    return f"rec_{name}"


# --------------------------------------------------------------------------------------
# Generator
# --------------------------------------------------------------------------------------


def output_expr(spec, endpoint: str) -> str:
    module, port = endpoint.split(".")
    mtype = spec["modules"][module]["type"]
    attr = OUT_ATTR[(mtype, port)]
    return f"{var(module)}_config_msg" if attr is None else f"{var(module)}.{attr}"


def vizard_period(spec: dict[str, Any], duration_s: float, max_frames: int) -> float:
    """Vizard task period: the dynamics step, stretched (integer multiple) to stay below ``max_frames``."""
    dt = float(spec["simulation"]["dt_s"])
    return dt * max(1, math.ceil(duration_s / dt / max_frames))


def generate_script(spec: dict[str, Any], duration_s: float, *, session_id: str = "standalone",
                    spec_version: int = 0, vizard: bool = False, vizard_dt_s: float | None = None) -> str:
    """Return the scenario source for ``spec`` running for ``duration_s`` seconds.

    With ``vizard=True`` the script also writes ``vizard.bin`` for Basilisk's Vizard 3-D visualizer.
    """
    for name in list(spec["modules"]) + list(spec["recorders"]):
        if not NAME_RE.match(name):  # defense in depth; names were validated on input
            raise ValidationError(f"invalid name {name!r}")
    if not NAME_RE.match(session_id.replace("-", "_")) and session_id != "standalone":
        session_id = "session"

    modules = spec["modules"]
    types_used = {m["type"] for m in modules.values()}
    has_fsw = any(MODULE_TYPES[t].category == "fsw" for t in types_used)
    sim = spec["simulation"]
    integrator = sim.get("integrator", "rk4")

    lines: list[str] = []
    w = lines.append

    # ---- header and imports
    w("#!/usr/bin/env python3")
    w(f"# Generated by basilisk-mcp {__version__} (session {session_id}, spec version {int(spec_version)}).")
    w("# Standalone Basilisk scenario: pip install bsk numpy, then")
    w("#     python scenario.py --output results/")
    w('"""Basilisk scenario generated from a basilisk-mcp session spec."""')
    w("")
    w("import argparse")
    w("import json")
    w("import pathlib")
    w("import time")
    w("")
    w("import numpy as np")
    w("")
    w("from Basilisk import __version__ as BSK_VERSION")
    utilities = ["SimulationBaseClass", "macros"]
    if any(m["params"].get("orbit") for m in modules.values() if m["type"] == "spacecraft"):
        utilities.append("orbitalMotion")
    if "gravity" in types_used:
        utilities.append("simIncludeGravBody")
    if "reaction_wheels" in types_used:
        utilities.append("simIncludeRW")
    if vizard:
        utilities.append("vizSupport")
    w(f"from Basilisk.utilities import {', '.join(utilities)}")
    sim_mods = sorted({SIM_IMPORTS[t] for t in types_used if t in SIM_IMPORTS})
    if integrator != "rk4" and "spacecraft" in types_used:
        sim_mods.append("svIntegrators")
    if sim_mods:
        w(f"from Basilisk.simulation import {', '.join(sim_mods)}")
    fsw_mods = sorted({FSW_IMPORTS[t] for t in types_used if t in FSW_IMPORTS})
    if fsw_mods:
        w(f"from Basilisk.fswAlgorithms import {', '.join(fsw_mods)}")
    if "mrp_feedback" in types_used:
        w("from Basilisk.architecture import messaging")
    if any(m["type"] == "gravity" and m["params"]["j2"] for m in modules.values()):
        w("from Basilisk.simulation.gravityEffector import SphericalHarmonicsGravityModel")
    w("")
    w(f"DURATION_S = {num(duration_s)}  # [s]")
    w(f"DYNAMICS_DT_S = {num(sim['dt_s'])}  # [s]")
    if has_fsw:
        w(f"FSW_DT_S = {num(task_dt(spec, 'fsw'))}  # [s]")
    if vizard:
        w(f"VIZARD_DT_S = {num(vizard_dt_s or sim['dt_s'])}  # [s] Vizard frame period")
    w("")
    w("")
    w("def run(output_dir: pathlib.Path) -> dict:")
    w('    """Build the simulation, run it, and write results.npz and summary.json."""')
    w("    sim = SimulationBaseClass.SimBaseClass()")
    w('    process = sim.CreateNewProcess("bskMcpProcess")')
    w('    process.addTask(sim.CreateNewTask("dynamicsTask", macros.sec2nano(DYNAMICS_DT_S)))')
    if has_fsw:
        w('    process.addTask(sim.CreateNewTask("fswTask", macros.sec2nano(FSW_DT_S)))')
    if vizard:
        w('    process.addTask(sim.CreateNewTask("vizardTask", macros.sec2nano(VIZARD_DT_S)))')
    w("    gravity_info = {}")

    body: list[str] = []
    b = body.append

    def add_to_task(name: str, mtype_name: str) -> None:
        mt = MODULE_TYPES[mtype_name]
        b(f'sim.AddModelToTask("{TASK[mt.category]}", {var(name)}, {int(mt.priority)})')

    # ---- phase 1: spacecraft hubs
    for name, mod in modules.items():
        if mod["type"] != "spacecraft":
            continue
        p, v = mod["params"], var(name)
        b("")
        b(f"# --- spacecraft '{name}'")
        b(f"{v} = spacecraft.Spacecraft()")
        b(f'{v}.ModelTag = "{name}"')
        b(f"{v}.hub.mHub = {num(p['mass_kg'])}  # [kg]")
        b(f"{v}.hub.r_BcB_B = [[0.0], [0.0], [0.0]]  # [m]")
        b(f"{v}.hub.IHubPntBc_B = {mat(p['inertia_kgm2'])}  # [kg*m^2]")
        if integrator != "rk4":
            cls = enum(INTEGRATORS[integrator], set(INTEGRATORS.values()))[1:-1]
            b(f"{v}_integrator = svIntegrators.{cls}({v})")
            b(f"{v}.setIntegrator({v}_integrator)")
        add_to_task(name, "spacecraft")

    # ---- phase 2: everything else, in insertion order
    for name, mod in modules.items():
        t, p, v = mod["type"], mod["params"], var(name)
        if t == "spacecraft":
            continue
        b("")
        b(f"# --- {t} '{name}'")
        if t == "gravity":
            factory = GRAVITY_FACTORY[p["central_body"]]
            b(f"{v}_factory = simIncludeGravBody.gravBodyFactory()")
            b(f"{v}_body = {v}_factory.{factory}()")
            b(f"{v}_body.isCentralBody = True")
            if p["j2"]:
                j2 = J2_MODELS[p["central_body"]]
                b(f"# J2-only zonal field, identical to Basilisk's {p['central_body']} *-J2-only.txt data file")
                b(f"{v}_harmonics = SphericalHarmonicsGravityModel()")
                b(f"{v}_harmonics.muBody = {num(j2['mu_m3ps2'])}  # [m^3/s^2]")
                b(f"{v}_harmonics.radEquator = {num(j2['radius_m'])}  # [m]")
                b(f"{v}_harmonics.cBar = [[1.0], [0.0, 0.0], [{num(j2['c20_normalized'])}, 0.0, 0.0]]  # [-]")
                b(f"{v}_harmonics.sBar = [[0.0], [0.0, 0.0], [0.0, 0.0, 0.0]]  # [-]")
                b(f"{v}_harmonics.maxDeg = 2")
                b(f"{v}_body.gravityModel = {v}_harmonics")
            for sc in p["attach_to"]:
                b(f"{v}_factory.addBodiesTo({var(sc)})")
            b(f'gravity_info["{name}"] = {{"mu_m3ps2": float({v}_body.mu), '
              f'"radius_m": float({v}_body.radEquator), "central_body": "{p["central_body"]}"}}')
        elif t == "ext_force_torque":
            b(f"{v} = extForceTorque.ExtForceTorque()")
            b(f'{v}.ModelTag = "{name}"')
            if any(p["torque_B_Nm"]):
                b(f"{v}.extTorquePntB_B = {col(p['torque_B_Nm'])}  # [N*m]")
            if any(p["force_B_N"]):
                b(f"{v}.extForce_B = {col(p['force_B_N'])}  # [N]")
            b(f"{var(p['attach_to'])}.addDynamicEffector({v})")
            add_to_task(name, t)
        elif t == "simple_nav":
            b(f"{v} = simpleNav.SimpleNav()")
            b(f'{v}.ModelTag = "{name}"')
            add_to_task(name, t)
        elif t == "reaction_wheels":
            model = enum(p["model"], RW_MODELS)
            speeds = p.get("initial_speeds_rpm") or [0.0] * len(p["spin_axes_B"])
            b(f"{v}_factory = simIncludeRW.rwFactory()")
            for axis, speed in zip(p["spin_axes_B"], speeds, strict=True):
                b(f"{v}_factory.create({model}, {row(axis)}, maxMomentum={num(p['max_momentum_Nms'])}, "
                  f"Omega={num(speed)})  # [N*m*s], [rpm]")
            b(f"{v} = reactionWheelStateEffector.ReactionWheelStateEffector()")
            b(f'{v}.ModelTag = "{name}"')
            b(f"{v}_factory.addToSpacecraft({v}.ModelTag, {v}, {var(p['attach_to'])})")
            add_to_task(name, t)
            b(f"{v}_config_msg = {v}_factory.getConfigMessage()")
        elif t == "inertial3d":
            b(f"{v} = inertial3D.inertial3D()")
            b(f'{v}.ModelTag = "{name}"')
            b(f"{v}.sigma_R0N = {row(p['sigma_R0N'])}  # [-]")
            add_to_task(name, t)
        elif t == "hill_point":
            b(f"{v} = hillPoint.hillPoint()")
            b(f'{v}.ModelTag = "{name}"')
            add_to_task(name, t)
        elif t == "att_tracking_error":
            b(f"{v} = attTrackingError.attTrackingError()")
            b(f'{v}.ModelTag = "{name}"')
            if any(p["sigma_R0R"]):
                b(f"{v}.sigma_R0R = {row(p['sigma_R0R'])}  # [-]")
            add_to_task(name, t)
        elif t == "mrp_feedback":
            b(f"{v} = mrpFeedback.mrpFeedback()")
            b(f'{v}.ModelTag = "{name}"')
            b(f"{v}.K = {num(p['K'])}  # [N*m]")
            b(f"{v}.Ki = {num(p['Ki'])}  # [1/s], negative disables integral feedback")
            b(f"{v}.P = {num(p['P'])}  # [N*m*s]")
            if p.get("integral_limit") is None:
                b(f"{v}.integralLimit = 2.0 / {v}.Ki * 0.1  # [N*m]")
            else:
                b(f"{v}.integralLimit = {num(p['integral_limit'])}  # [N*m]")
            add_to_task(name, t)
            inertia = modules[p["inertia_from"]]["params"]["inertia_kgm2"]
            flat = [x for r in inertia for x in r]
            b(f"{v}_vehicle_config = messaging.VehicleConfigMsgPayload()")
            b(f"{v}_vehicle_config.ISCPntB_B = {row(flat)}  # [kg*m^2]")
            b(f"{v}_vehicle_config_msg = messaging.VehicleConfigMsg().write({v}_vehicle_config)")
            b(f"{v}.vehConfigInMsg.subscribeTo({v}_vehicle_config_msg)")
        elif t == "rw_motor_torque":
            flat = [x for axis in p["control_axes_B"] for x in axis]
            b(f"{v} = rwMotorTorque.rwMotorTorque()")
            b(f'{v}.ModelTag = "{name}"')
            b(f"{v}.controlAxes_B = {row(flat)}  # [-]")
            add_to_task(name, t)
        else:  # pragma: no cover
            raise ValidationError(f"no code generator for module type {t}")

    # ---- phase 3: initial spacecraft states (needs gravity parameters)
    for name, mod in modules.items():
        if mod["type"] != "spacecraft":
            continue
        p, v = mod["params"], var(name)
        b("")
        b(f"# --- initial state of '{name}'")
        if p.get("orbit") is not None:
            o = p["orbit"]
            g = var(gravity_of(spec, name))
            b(f"{v}_oe = orbitalMotion.ClassicElements()")
            if o.get("semi_major_axis_km") is not None:
                b(f"{v}_oe.a = {num(o['semi_major_axis_km'] * 1000.0)}  # [m]")
            else:
                b(f"{v}_oe.a = {g}_body.radEquator + {num(o['altitude_km'] * 1000.0)}  # [m]")
            b(f"{v}_oe.e = {num(o['eccentricity'])}  # [-]")
            b(f"{v}_oe.i = {num(o['inclination_deg'])} * macros.D2R  # [rad]")
            b(f"{v}_oe.Omega = {num(o['raan_deg'])} * macros.D2R  # [rad]")
            b(f"{v}_oe.omega = {num(o['arg_periapsis_deg'])} * macros.D2R  # [rad]")
            b(f"{v}_oe.f = {num(o['true_anomaly_deg'])} * macros.D2R  # [rad]")
            b(f"{v}_rN, {v}_vN = orbitalMotion.elem2rv({g}_body.mu, {v}_oe)")
            b(f"{v}.hub.r_CN_NInit = {v}_rN  # [m]")
            b(f"{v}.hub.v_CN_NInit = {v}_vN  # [m/s]")
        else:
            b(f"{v}.hub.r_CN_NInit = {col(p['position_m'])}  # [m]")
            b(f"{v}.hub.v_CN_NInit = {col(p['velocity_mps'])}  # [m/s]")
        if any(p["sigma_BN"]):
            b(f"{v}.hub.sigma_BNInit = {col(p['sigma_BN'])}  # [-]")
        if any(p["omega_BN_B_radps"]):
            b(f"{v}.hub.omega_BN_BInit = {col(p['omega_BN_B_radps'])}  # [rad/s]")

    # ---- phase 4: message connections
    if spec["connections"]:
        b("")
        b("# --- message connections")
    for c in spec["connections"]:
        t_mod, t_port = c["target"].split(".")
        attr = IN_ATTR[(modules[t_mod]["type"], t_port)]
        b(f"{var(t_mod)}.{attr}.subscribeTo({output_expr(spec, c['source'])})")

    # ---- phase 5: recorders
    rec_meta: dict[str, dict[str, Any]] = {}
    if spec["recorders"]:
        b("")
        b("# --- recorders")
    for rname, rec in spec["recorders"].items():
        s_mod, s_port = rec["source"].split(".")
        mtype = MODULE_TYPES[modules[s_mod]["type"]]
        msg_type = mtype.outputs[s_port].msg_type
        rv = rec_var(rname)
        b(f"{rv} = {output_expr(spec, rec['source'])}.recorder(macros.sec2nano({num(rec['sample_dt_s'])}))")
        b(f'sim.AddModelToTask("{TASK[mtype.category]}", {rv})')
        widths = {}
        for vname, vdef in MESSAGE_TYPES[msg_type].items():
            widths[vname] = vdef.width or wheel_count(spec, rec["source"]) or 36
        rec_meta[rname] = {"source": rec["source"], "msg_type": msg_type, "variables": widths}

    # ---- phase 6: Vizard 3-D visualization file
    if vizard:
        spacecraft_names = [n for n, m in modules.items() if m["type"] == "spacecraft"]
        wheels = {m["params"]["attach_to"]: n for n, m in modules.items() if m["type"] == "reaction_wheels"}
        b("")
        b("# --- Vizard 3-D visualization file (open with: Vizard -loadFile vizard.bin)")
        b("vizard_file = None")
        b("if vizSupport.vizFound:")
        b('    vizard_file = output_dir / "vizard.bin"')
        b("    viz = vizSupport.enableUnityVisualization(")
        b(f'        sim, "vizardTask", [{", ".join(var(n) for n in spacecraft_names)}],')
        b("        saveFile=str(vizard_file),")
        if wheels:
            rw_list = ", ".join(var(wheels[n]) if n in wheels else "None" for n in spacecraft_names)
            b(f"        rwEffectorList=[{rw_list}],")
        b("    )")
        b("    viz.settings.spacecraftCSon = 1  # show body axes")
        b("    viz.settings.showSpacecraftLabels = 1")
        for n in spacecraft_names:
            if n in wheels:
                b(f"    vizSupport.setActuatorGuiSetting(viz, spacecraftName={var(n)}.ModelTag, "
                  "viewRWPanel=True, viewRWHUD=True)")

    for line in body:
        lines.append(("    " + line) if line else "")

    # ---- run and save
    w("")
    w("    # --- run")
    w("    start = time.perf_counter()")
    w("    sim.InitializeSimulation()")
    w("    sim.ConfigureStopTime(macros.sec2nano(DURATION_S))")
    w("    sim.ExecuteSimulation()")
    w("    wall_time_s = time.perf_counter() - start")
    w("")
    w("    # --- save recorded data")
    w("    data = {}")
    w("    recorders = {}")
    for rname, meta in rec_meta.items():
        rv = rec_var(rname)
        w(f'    data["{rname}__time_s"] = np.asarray({rv}.times(), dtype=float) * macros.NANO2SEC')
        for vname, width in meta["variables"].items():
            w(f'    data["{rname}__{vname}"] = np.asarray({rv}.{vname}, dtype=float).reshape('
              f'len(data["{rname}__time_s"]), -1)[:, :{int(width)}]')
        w(f'    recorders["{rname}"] = {{"source": "{meta["source"]}", "msg_type": "{meta["msg_type"]}", '
          f'"samples": int(len(data["{rname}__time_s"])), "variables": {dict(meta["variables"])!r}}}')
    w('    np.savez(output_dir / "results.npz", **data)')
    w("    summary = {")
    w('        "basilisk_version": BSK_VERSION,')
    w('        "duration_s": DURATION_S,')
    w('        "wall_time_s": wall_time_s,')
    w('        "gravity": gravity_info,')
    sc_grav = {n: gravity_of(spec, n) for n, m in modules.items() if m["type"] == "spacecraft"}
    w(f'        "spacecraft_gravity": {sc_grav!r},')
    if vizard:
        w('        "vizard_file": vizard_file.name if vizard_file is not None else None,')
    w('        "recorders": recorders,')
    w("    }")
    w('    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")')
    w("    return summary")
    w("")
    w("")
    w('if __name__ == "__main__":')
    w("    parser = argparse.ArgumentParser(description=__doc__)")
    w('    parser.add_argument("--output", default="results", help="directory for results.npz and summary.json")')
    w("    out = pathlib.Path(parser.parse_args().output)")
    w("    out.mkdir(parents=True, exist_ok=True)")
    w("    result = run(out)")
    w('    print(json.dumps({"status": "ok", "wall_time_s": result["wall_time_s"]}))')
    w("")

    source = "\n".join(lines)
    ast.parse(source)  # final syntax gate before anything is executed
    return source
