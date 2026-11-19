"""Declarative session spec: editing operations and whole-session validation.

A spec is plain JSON::

    {
      "simulation": {"dt_s": 0.1, "fsw_dt_s": null, "integrator": "rk4"},
      "modules":    {"<name>": {"type": "<module type>", "params": {...}}},
      "connections": [{"source": "<module>.<output port>", "target": "<module>.<input port>"}],
      "recorders":  {"<name>": {"source": "<module>.<output port>", "sample_dt_s": 1.0}}
    }

Edit functions validate their own arguments eagerly. ``validate_spec`` checks the
session as a whole (references, required inputs, limits) and returns every problem
it finds so a client can fix them in one pass.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from .config import Settings
from .errors import Conflict, LimitExceeded, NotFound, ValidationError
from .registry import (
    INTEGRATORS,
    MESSAGE_TYPES,
    MODULE_TYPES,
    Param,
    check_name,
    get_module_type,
    normalize_value,
)

SIMULATION_PARAMS = {
    "dt_s": Param("float", "Dynamics integration step.", "s", default=0.1, minimum=1e-4, maximum=3600.0),
    "fsw_dt_s": Param("float", "Flight-software task period (defaults to dt_s).", "s", nullable=True,
                      minimum=1e-4, maximum=3600.0),
    "integrator": Param("enum", "Spacecraft state integrator.", default="rk4", choices=tuple(INTEGRATORS)),
}


def new_spec() -> dict[str, Any]:
    return {
        "simulation": {"dt_s": 0.1, "fsw_dt_s": None, "integrator": "rk4"},
        "modules": {},
        "connections": [],
        "recorders": {},
    }


def task_dt(spec: dict[str, Any], category: str) -> float:
    sim = spec["simulation"]
    if category == "fsw" and sim.get("fsw_dt_s"):
        return float(sim["fsw_dt_s"])
    return float(sim["dt_s"])


def parse_endpoint(endpoint: Any, what: str) -> tuple[str, str]:
    if not isinstance(endpoint, str) or endpoint.count(".") != 1:
        raise ValidationError(f"{what} must look like '<module>.<port>', got {endpoint!r}.",
                              "Example: 'nav.att' or 'sc.state'.")
    module, port = endpoint.split(".")
    check_name(module, f"module name in {what}")
    if not port or not port.replace("_", "").isalnum() or len(port) > 32:
        raise ValidationError(f"Invalid port name {port!r} in {what}.")
    return module, port


def _module(spec: dict[str, Any], name: str) -> dict[str, Any]:
    if name not in spec["modules"]:
        raise NotFound("Module", name, sorted(spec["modules"]))
    return spec["modules"][name]


def _output_port(spec: dict[str, Any], endpoint: str):
    module, port = parse_endpoint(endpoint, "source")
    mtype = MODULE_TYPES[_module(spec, module)["type"]]
    if port not in mtype.outputs:
        raise ValidationError(f"Module '{module}' ({mtype.name}) has no output port '{port}'.",
                              f"Outputs: {sorted(mtype.outputs) or 'none'}")
    return module, port, mtype.outputs[port]


def _input_port(spec: dict[str, Any], endpoint: str):
    module, port = parse_endpoint(endpoint, "target")
    mtype = MODULE_TYPES[_module(spec, module)["type"]]
    if port not in mtype.inputs:
        raise ValidationError(f"Module '{module}' ({mtype.name}) has no input port '{port}'.",
                              f"Inputs: {sorted(mtype.inputs) or 'none'}")
    return module, port, mtype.inputs[port]


# --------------------------------------------------------------------------------------
# Edit operations (mutate spec in place)
# --------------------------------------------------------------------------------------


def add_module(spec, name, module_type, params, settings: Settings) -> dict[str, Any]:
    check_name(name, "module name")
    mtype = get_module_type(module_type)
    if name in spec["modules"]:
        raise Conflict(f"A module named '{name}' already exists.", "Use set_params to change it or pick a new name.")
    if name in spec["recorders"]:
        raise Conflict(f"'{name}' is already used by a recorder.")
    if len(spec["modules"]) >= settings.max_modules:
        raise LimitExceeded(f"Sessions are limited to {settings.max_modules} modules.")
    normalized = mtype.normalize_params(params)
    spec["modules"][name] = {"type": mtype.name, "params": normalized}
    return spec["modules"][name]


def set_params(spec, name, params) -> dict[str, Any]:
    module = _module(spec, name)
    mtype = MODULE_TYPES[module["type"]]
    module["params"] = mtype.normalize_params(params, base=module["params"])
    return module


def remove_module(spec, name) -> dict[str, list]:
    _module(spec, name)
    del spec["modules"][name]
    removed_conn = [c for c in spec["connections"]
                    if c["source"].split(".")[0] == name or c["target"].split(".")[0] == name]
    spec["connections"] = [c for c in spec["connections"] if c not in removed_conn]
    removed_rec = [r for r, v in spec["recorders"].items() if v["source"].split(".")[0] == name]
    for r in removed_rec:
        del spec["recorders"][r]
    return {"removed_connections": removed_conn, "removed_recorders": removed_rec}


def connect(spec, source, target) -> dict[str, str]:
    s_mod, s_port, s_def = _output_port(spec, source)
    t_mod, t_port, t_def = _input_port(spec, target)
    if s_def.msg_type != t_def.msg_type:
        raise ValidationError(
            f"Message type mismatch: {source} produces {s_def.msg_type} but {target} expects {t_def.msg_type}.",
            _compatible_outputs_hint(spec, t_def.msg_type),
        )
    if s_mod == t_mod:
        raise ValidationError("A module cannot be connected to itself.")
    canonical_target = f"{t_mod}.{t_port}"
    for c in spec["connections"]:
        if c["target"] == canonical_target:
            raise Conflict(f"Input {canonical_target} is already connected to {c['source']}.",
                           "Call disconnect on the target first.")
    conn = {"source": f"{s_mod}.{s_port}", "target": canonical_target}
    spec["connections"].append(conn)
    return conn


def disconnect(spec, target) -> dict[str, str]:
    module, port = parse_endpoint(target, "target")
    for c in spec["connections"]:
        if c["target"] == f"{module}.{port}":
            spec["connections"].remove(c)
            return c
    raise NotFound("Connection to input", target, [c["target"] for c in spec["connections"]])


def add_recorder(spec, name, source, sample_dt_s, settings: Settings) -> dict[str, Any]:
    check_name(name, "recorder name")
    if name in spec["recorders"] or name in spec["modules"]:
        raise Conflict(f"The name '{name}' is already used.")
    if len(spec["recorders"]) >= settings.max_recorders:
        raise LimitExceeded(f"Sessions are limited to {settings.max_recorders} recorders.")
    mod, port, pdef = _output_port(spec, source)
    if not MESSAGE_TYPES[pdef.msg_type]:
        raise ValidationError(f"{source} ({pdef.msg_type}) is a configuration message and cannot be recorded.")
    dt = normalize_value(Param("float", "", "s", minimum=1e-4, maximum=1e7), sample_dt_s, "sample_dt_s")
    rec = {"source": f"{mod}.{port}", "sample_dt_s": dt}
    spec["recorders"][name] = rec
    return rec


def remove_recorder(spec, name) -> None:
    if name not in spec["recorders"]:
        raise NotFound("Recorder", name, sorted(spec["recorders"]))
    del spec["recorders"][name]


def set_simulation(spec, dt_s=None, fsw_dt_s=None, integrator=None, clear_fsw_dt=False) -> dict[str, Any]:
    sim = dict(spec["simulation"])
    if dt_s is not None:
        sim["dt_s"] = normalize_value(SIMULATION_PARAMS["dt_s"], dt_s, "dt_s")
    if fsw_dt_s is not None:
        sim["fsw_dt_s"] = normalize_value(SIMULATION_PARAMS["fsw_dt_s"], fsw_dt_s, "fsw_dt_s")
    if clear_fsw_dt:
        sim["fsw_dt_s"] = None
    if integrator is not None:
        sim["integrator"] = normalize_value(SIMULATION_PARAMS["integrator"], integrator, "integrator")
    spec["simulation"] = sim
    return sim


# --------------------------------------------------------------------------------------
# Whole-session validation
# --------------------------------------------------------------------------------------


def _compatible_outputs_hint(spec, msg_type: str) -> str:
    found = [f"{m}.{p}" for m, mod in spec["modules"].items()
             for p, port in MODULE_TYPES[mod["type"]].outputs.items() if port.msg_type == msg_type]
    if found:
        return f"Compatible {msg_type} outputs in this session: {', '.join(found)}"
    producers = sorted({f"{t.name}.{p}" for t in MODULE_TYPES.values()
                        for p, port in t.outputs.items() if port.msg_type == msg_type})
    return f"No {msg_type} output exists yet. Module ports that produce it: {', '.join(producers)}"


def gravity_of(spec, sc_name: str) -> str | None:
    for name, mod in spec["modules"].items():
        if mod["type"] == "gravity" and sc_name in mod["params"]["attach_to"]:
            return name
    return None


def source_of(spec, target: str) -> str | None:
    for c in spec["connections"]:
        if c["target"] == target:
            return c["source"]
    return None


def wheel_count(spec, endpoint: str) -> int | None:
    """Number of wheels behind an RW-sized output (RWSpeedMsg / ArrayMotorTorqueMsg)."""
    module, port = endpoint.split(".")
    mod = spec["modules"].get(module)
    if mod is None:
        return None
    if mod["type"] == "reaction_wheels":
        return len(mod["params"]["spin_axes_B"])
    if mod["type"] == "rw_motor_torque":
        src = source_of(spec, f"{module}.rw_params")
        return wheel_count(spec, src) if src else None
    return None


def validate_spec(spec, settings: Settings, duration_s: float | None = None) -> list[str]:
    problems: list[str] = []
    modules = spec["modules"]
    if not modules:
        return ["The session has no modules. Add a 'spacecraft' (and usually 'gravity') or use a template."]

    if not any(m["type"] == "spacecraft" for m in modules.values()):
        problems.append("At least one 'spacecraft' module is required.")

    # References between modules.
    for name, mod in modules.items():
        mtype = MODULE_TYPES[mod["type"]]
        for key, param in mtype.params.items():
            if param.kind not in ("ref", "refs") or mod["params"].get(key) is None:
                continue
            refs = mod["params"][key] if param.kind == "refs" else [mod["params"][key]]
            for ref in refs:
                if ref not in modules:
                    problems.append(f"{name}.{key} refers to missing module '{ref}'.")
                elif modules[ref]["type"] != param.ref_type:
                    problems.append(f"{name}.{key} must refer to a '{param.ref_type}' module, "
                                    f"but '{ref}' is a '{modules[ref]['type']}'.")

    # Spacecraft: one gravity field each; orbit elements need gravity.
    for name, mod in modules.items():
        if mod["type"] != "spacecraft":
            continue
        fields = [g for g, m in modules.items() if m["type"] == "gravity" and name in m["params"]["attach_to"]]
        if len(fields) > 1:
            problems.append(f"Spacecraft '{name}' has several gravity modules attached ({fields}); use one.")
        if mod["params"].get("orbit") is not None and not fields:
            problems.append(f"Spacecraft '{name}' uses orbit elements but no 'gravity' module is attached to it.")
        if mod["params"].get("orbit") is None and mod["params"].get("position_m") is None:
            problems.append(f"Spacecraft '{name}' needs an initial 'orbit' or 'position_m'+'velocity_mps'.")
        for kind in ("reaction_wheels",):
            clusters = [m for m, d in modules.items() if d["type"] == kind and d["params"]["attach_to"] == name]
            if len(clusters) > 1:
                problems.append(f"Spacecraft '{name}' has several reaction wheel clusters ({clusters}); "
                                "put all wheels in one cluster.")

    # Orbits must stay above the central body surface.
    for name, mod in modules.items():
        if mod["type"] != "spacecraft" or mod["params"].get("orbit") is None:
            continue
        orbit = mod["params"]["orbit"]
        if orbit.get("semi_major_axis_km") is not None:
            grav = gravity_of(spec, name)
            if grav:
                body = modules[grav]["params"]["central_body"]
                radius_km = BODY_RADIUS_KM[body]
                periapsis = orbit["semi_major_axis_km"] * (1.0 - orbit["eccentricity"])
                if periapsis <= radius_km:
                    problems.append(f"Spacecraft '{name}': periapsis radius {periapsis:.1f} km is inside "
                                    f"{body} (radius {radius_km:.1f} km).")

    # Connections still valid, required inputs connected.
    connected = {c["target"]: c["source"] for c in spec["connections"]}
    for c in spec["connections"]:
        for end in ("source", "target"):
            if c[end].split(".")[0] not in modules:
                problems.append(f"Connection {c['source']} -> {c['target']} refers to a missing module.")
    for name, mod in modules.items():
        mtype = MODULE_TYPES[mod["type"]]
        for port, pdef in mtype.inputs.items():
            if pdef.required and f"{name}.{port}" not in connected:
                problems.append(f"Required input {name}.{port} ({pdef.msg_type}) is not connected. "
                                + _compatible_outputs_hint(spec, pdef.msg_type))
        if mod["type"] == "mrp_feedback":
            has_p, has_s = f"{name}.rw_params" in connected, f"{name}.rw_speeds" in connected
            if has_p != has_s:
                problems.append(f"{name}: connect 'rw_params' and 'rw_speeds' together (or neither).")
        if mod["type"] == "rw_motor_torque":
            src = connected.get(f"{name}.rw_params")
            n = wheel_count(spec, src) if src else None
            if n is not None and len(mod["params"]["control_axes_B"]) > n:
                problems.append(f"{name}: {len(mod['params']['control_axes_B'])} control axes but only {n} wheels.")
        if mod["type"] == "reaction_wheels":
            target = connected.get(f"{name}.motor_torque")
            if target is None:
                problems.append(f"Reaction wheels '{name}' have no motor torque command; connect "
                                "an rw_motor_torque.motor_torque output to "
                                f"{name}.motor_torque (otherwise the wheels just coast).")

    # Task rates and limits.
    sim = spec["simulation"]
    has_fsw = any(MODULE_TYPES[m["type"]].category == "fsw" for m in modules.values())
    for rec_name, rec in spec["recorders"].items():
        mod_name = rec["source"].split(".")[0]
        if mod_name not in modules:
            problems.append(f"Recorder '{rec_name}' records from missing module '{mod_name}'.")
            continue
        dt = task_dt(spec, MODULE_TYPES[modules[mod_name]["type"]].category)
        ratio = rec["sample_dt_s"] / dt
        if ratio < 1 - 1e-9 or abs(ratio - round(ratio)) > 1e-6:
            problems.append(f"Recorder '{rec_name}': sample_dt_s={rec['sample_dt_s']} must be a positive integer "
                            f"multiple of its task step {dt} s.")
    if not spec["recorders"]:
        problems.append("No recorders defined: add_recorder so the run produces data "
                        "(e.g. source 'sc.state').")

    if duration_s is not None:
        if not (isinstance(duration_s, (int, float)) and math.isfinite(duration_s) and duration_s > 0):
            problems.append("duration_s must be a positive number.")
        else:
            if duration_s > settings.max_sim_duration_s:
                problems.append(f"duration_s={duration_s} exceeds the limit of {settings.max_sim_duration_s} s.")
            steps = duration_s / sim["dt_s"]
            if steps > settings.max_steps:
                problems.append(f"{steps:.0f} dynamics steps exceed max_steps={settings.max_steps}; "
                                "increase dt_s or shorten duration_s.")
            if has_fsw and duration_s / task_dt(spec, "fsw") > settings.max_steps:
                problems.append(f"FSW steps exceed max_steps={settings.max_steps}; increase fsw_dt_s.")
            for rec_name, rec in spec["recorders"].items():
                samples = duration_s / rec["sample_dt_s"]
                if samples > settings.max_recorded_samples:
                    problems.append(f"Recorder '{rec_name}' would store {samples:.0f} samples "
                                    f"(limit {settings.max_recorded_samples}); increase sample_dt_s.")
    return problems


BODY_RADIUS_KM = {
    "earth": 6378.1366,
    "moon": 1737.4,
    "mars": 3396.19,
    "sun": 695000.0,
    "venus": 6051.8,
    "jupiter": 71492.0,
}


def describe_spec(spec) -> dict[str, Any]:
    """A compact copy of the spec for tool responses."""
    return copy.deepcopy(spec)
