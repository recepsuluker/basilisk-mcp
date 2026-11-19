"""Whitelisted catalog of Basilisk module types, their parameters, and message ports.

The registry is pure data plus validation. It never imports Basilisk, so the MCP
server can validate sessions even when Basilisk lives in a different interpreter.

Units are SI and appear in every physical parameter name (``mass_kg``,
``torque_B_Nm``, ``inclination_deg``). Frame suffixes follow Basilisk: ``_B``
body frame, ``_N`` inertial frame, ``_R`` reference frame.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from .errors import ValidationError

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
NAME_RULE = "lowercase letters, digits and underscores, starting with a letter, at most 32 characters"
RESERVED_NAMES = frozenset({"sim", "process", "np", "json", "math", "macros", "messaging", "time", "args"})

MAX_ABS_VALUE = 1e30


def check_name(name: Any, what: str = "name") -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValidationError(f"Invalid {what} {name!r}: use {NAME_RULE}.", "Example: 'sc', 'rw_cluster', 'control'.")
    if name in RESERVED_NAMES:
        raise ValidationError(f"The {what} {name!r} is reserved.", f"Reserved: {sorted(RESERVED_NAMES)}")
    return name


# --------------------------------------------------------------------------------------
# Parameter schema
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Param:
    kind: str  # float | int | bool | enum | vec3 | mat3 | vec3_list | float_list | ref | refs | orbit
    description: str
    unit: str = "-"
    default: Any = None
    required: bool = False
    nullable: bool = False
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: bool = False
    choices: tuple = ()
    ref_type: str | None = None
    min_items: int | None = None
    max_items: int | None = None

    def schema(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "unit": self.unit, "description": self.description}
        if self.default is not None:
            out["default"] = self.default
        if self.required:
            out["required"] = True
        if self.nullable:
            out["optional"] = True
        if self.minimum is not None:
            out["exclusive_minimum" if self.exclusive_minimum else "minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        if self.choices:
            out["choices"] = list(self.choices)
        if self.ref_type:
            out["references_module_type"] = self.ref_type
        if self.min_items is not None:
            out["min_items"] = self.min_items
        if self.max_items is not None:
            out["max_items"] = self.max_items
        if self.kind == "orbit":
            out["fields"] = {k: v.schema() for k, v in ORBIT_FIELDS.items()}
        return out


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{where} must be a number, got {type(value).__name__} {value!r}.")
    number = float(value)
    if not math.isfinite(number) or abs(number) > MAX_ABS_VALUE:
        raise ValidationError(f"{where} must be a finite number with magnitude <= 1e30, got {value!r}.")
    return number


def _bounded(param: Param, number: float, where: str) -> float:
    if param.minimum is not None:
        if param.exclusive_minimum and not number > param.minimum:
            raise ValidationError(f"{where} must be > {param.minimum} {param.unit}, got {number}.")
        if not param.exclusive_minimum and number < param.minimum:
            raise ValidationError(f"{where} must be >= {param.minimum} {param.unit}, got {number}.")
    if param.maximum is not None and number > param.maximum:
        raise ValidationError(f"{where} must be <= {param.maximum} {param.unit}, got {number}.")
    return number


def _vec3(value: Any, where: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValidationError(f"{where} must be a list of 3 numbers, got {value!r}.", "Example: [0.1, 0.2, -0.3]")
    return [_number(v, f"{where}[{i}]") for i, v in enumerate(value)]


def _mat3(value: Any, where: str) -> list[list[float]]:
    hint = "Use 3 numbers (diagonal), 9 numbers (row-major) or a 3x3 nested list."
    if not isinstance(value, (list, tuple)):
        raise ValidationError(f"{where} must be a 3x3 matrix.", hint)
    if len(value) == 3 and all(isinstance(r, (list, tuple)) for r in value):
        rows = [_vec3(r, f"{where}[{i}]") for i, r in enumerate(value)]
    elif len(value) == 3:
        d = _vec3(value, where)
        rows = [[d[0], 0.0, 0.0], [0.0, d[1], 0.0], [0.0, 0.0, d[2]]]
    elif len(value) == 9:
        flat = [_number(v, f"{where}[{i}]") for i, v in enumerate(value)]
        rows = [flat[0:3], flat[3:6], flat[6:9]]
    else:
        raise ValidationError(f"{where} has {len(value)} entries.", hint)
    return rows


def _check_inertia(rows: list[list[float]], where: str) -> None:
    scale = max(abs(x) for r in rows for x in r) or 1.0
    for i in range(3):
        for j in range(3):
            if abs(rows[i][j] - rows[j][i]) > 1e-9 * scale:
                raise ValidationError(f"{where} must be symmetric.")
    a, b, c = rows
    m1 = a[0]
    m2 = a[0] * b[1] - a[1] * b[0]
    m3 = (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
          + a[2] * (b[0] * c[1] - b[1] * c[0]))
    if not (m1 > 0 and m2 > 0 and m3 > 0):
        raise ValidationError(f"{where} must be positive definite (a physical inertia tensor).")
    d = [rows[0][0], rows[1][1], rows[2][2]]
    for k in range(3):
        if d[k] > d[(k + 1) % 3] + d[(k + 2) % 3] + 1e-9 * scale:
            raise ValidationError(f"{where} violates the triangle inequality of principal inertias.")


ORBIT_FIELDS: dict[str, Param] = {
    "semi_major_axis_km": Param("float", "Semi-major axis. Give this or altitude_km.", "km", nullable=True,
                                minimum=0.0, exclusive_minimum=True, maximum=1e10),
    "altitude_km": Param("float", "Altitude above the central body's equatorial radius, sets a = R + h.",
                         "km", nullable=True, minimum=0.0, maximum=1e10),
    "eccentricity": Param("float", "Eccentricity (elliptic orbits only).", "-", default=0.0,
                          minimum=0.0, maximum=0.99),
    "inclination_deg": Param("float", "Inclination.", "deg", default=0.0, minimum=0.0, maximum=180.0),
    "raan_deg": Param("float", "Right ascension of the ascending node.", "deg", default=0.0,
                      minimum=-360.0, maximum=360.0),
    "arg_periapsis_deg": Param("float", "Argument of periapsis.", "deg", default=0.0,
                               minimum=-360.0, maximum=360.0),
    "true_anomaly_deg": Param("float", "True anomaly at the initial epoch.", "deg", default=0.0,
                              minimum=-360.0, maximum=360.0),
}


def _orbit(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{where} must be an object of classical orbit elements.",
                              "Example: {'altitude_km': 500, 'inclination_deg': 97.4}")
    unknown = set(value) - set(ORBIT_FIELDS)
    if unknown:
        raise ValidationError(f"Unknown orbit field(s) {sorted(unknown)} in {where}.",
                              f"Valid fields: {sorted(ORBIT_FIELDS)}")
    out = {}
    for key, param in ORBIT_FIELDS.items():
        raw = value.get(key, param.default)
        if raw is None:
            out[key] = None
            continue
        out[key] = _bounded(param, _number(raw, f"{where}.{key}"), f"{where}.{key}")
    if (out["semi_major_axis_km"] is None) == (out["altitude_km"] is None):
        raise ValidationError(f"{where} needs exactly one of 'semi_major_axis_km' or 'altitude_km'.")
    return out


def normalize_value(param: Param, value: Any, where: str) -> Any:
    """Validate one parameter value and return its canonical JSON form."""
    if value is None:
        if param.nullable:
            return None
        raise ValidationError(f"{where} may not be null.")
    kind = param.kind
    if kind == "float":
        return _bounded(param, _number(value, where), where)
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(f"{where} must be an integer, got {value!r}.")
        return int(_bounded(param, float(value), where))
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValidationError(f"{where} must be true or false, got {value!r}.")
        return value
    if kind == "enum":
        if value not in param.choices:
            raise ValidationError(f"{where} must be one of {list(param.choices)}, got {value!r}.")
        return value
    if kind == "vec3":
        vec = _vec3(value, where)
        for i, v in enumerate(vec):
            _bounded(param, v, f"{where}[{i}]")
        return vec
    if kind == "mat3":
        return _mat3(value, where)
    if kind == "vec3_list":
        if not isinstance(value, (list, tuple)):
            raise ValidationError(f"{where} must be a list of 3-vectors.")
        if param.min_items is not None and len(value) < param.min_items:
            raise ValidationError(f"{where} needs at least {param.min_items} item(s).")
        if param.max_items is not None and len(value) > param.max_items:
            raise ValidationError(f"{where} allows at most {param.max_items} item(s).")
        vectors = [_vec3(v, f"{where}[{i}]") for i, v in enumerate(value)]
        for i, v in enumerate(vectors):
            if math.sqrt(sum(x * x for x in v)) < 1e-9:
                raise ValidationError(f"{where}[{i}] must not be the zero vector.")
        return vectors
    if kind == "float_list":
        if not isinstance(value, (list, tuple)):
            raise ValidationError(f"{where} must be a list of numbers.")
        if param.max_items is not None and len(value) > param.max_items:
            raise ValidationError(f"{where} allows at most {param.max_items} item(s).")
        return [_bounded(param, _number(v, f"{where}[{i}]"), f"{where}[{i}]") for i, v in enumerate(value)]
    if kind == "ref":
        return check_name(value, f"module reference in {where}")
    if kind == "refs":
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)) or not value:
            raise ValidationError(f"{where} must be a non-empty list of module names.")
        if param.max_items is not None and len(value) > param.max_items:
            raise ValidationError(f"{where} allows at most {param.max_items} item(s).")
        names = [check_name(v, f"module reference in {where}") for v in value]
        if len(set(names)) != len(names):
            raise ValidationError(f"{where} contains duplicate names.")
        return names
    if kind == "orbit":
        return _orbit(value, where)
    raise AssertionError(f"unknown parameter kind {kind}")  # pragma: no cover


# --------------------------------------------------------------------------------------
# Message types and ports
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Variable:
    width: int | None  # None: depends on the producing module (wheel count)
    unit: str
    description: str


MESSAGE_TYPES: dict[str, dict[str, Variable]] = {
    "SCStatesMsg": {
        "r_BN_N": Variable(3, "m", "inertial position of the body frame origin"),
        "v_BN_N": Variable(3, "m/s", "inertial velocity"),
        "sigma_BN": Variable(3, "-", "attitude MRP of body relative to inertial"),
        "omega_BN_B": Variable(3, "rad/s", "body angular rate in body frame"),
    },
    "NavAttMsg": {
        "sigma_BN": Variable(3, "-", "estimated attitude MRP"),
        "omega_BN_B": Variable(3, "rad/s", "estimated body rate"),
        "vehSunPntBdy": Variable(3, "-", "sun direction in body frame"),
    },
    "NavTransMsg": {
        "r_BN_N": Variable(3, "m", "estimated inertial position"),
        "v_BN_N": Variable(3, "m/s", "estimated inertial velocity"),
    },
    "AttRefMsg": {
        "sigma_RN": Variable(3, "-", "reference attitude MRP"),
        "omega_RN_N": Variable(3, "rad/s", "reference angular rate (inertial frame)"),
        "domega_RN_N": Variable(3, "rad/s^2", "reference angular acceleration (inertial frame)"),
    },
    "AttGuidMsg": {
        "sigma_BR": Variable(3, "-", "attitude tracking error MRP"),
        "omega_BR_B": Variable(3, "rad/s", "rate tracking error"),
        "omega_RN_B": Variable(3, "rad/s", "reference rate in body frame"),
        "domega_RN_B": Variable(3, "rad/s^2", "reference acceleration in body frame"),
    },
    "CmdTorqueBodyMsg": {
        "torqueRequestBody": Variable(3, "N*m", "commanded body torque"),
    },
    "RWSpeedMsg": {
        "wheelSpeeds": Variable(None, "rad/s", "reaction wheel speeds"),
    },
    "ArrayMotorTorqueMsg": {
        "motorTorque": Variable(None, "N*m", "reaction wheel motor torques"),
    },
    "RWArrayConfigMsg": {},  # configuration only, not recordable
}

#: Server-side derived variables available in query/plot/compare, per message type.
DERIVED_VARIABLES: dict[str, dict[str, Variable]] = {
    "SCStatesMsg": {
        "radius_km": Variable(1, "km", "distance from the central body center"),
        "speed_kmps": Variable(1, "km/s", "inertial speed"),
        "attitude_angle_deg": Variable(1, "deg", "principal rotation angle of sigma_BN"),
        "rate_norm_degps": Variable(1, "deg/s", "norm of omega_BN_B"),
        "raan_deg": Variable(1, "deg", "right ascension of the ascending node (unwrapped, from r x v)"),
        "inclination_deg": Variable(1, "deg", "orbit inclination (from r x v)"),
    },
    "NavTransMsg": {
        "radius_km": Variable(1, "km", "distance from the central body center"),
        "speed_kmps": Variable(1, "km/s", "inertial speed"),
        "raan_deg": Variable(1, "deg", "right ascension of the ascending node (unwrapped, from r x v)"),
        "inclination_deg": Variable(1, "deg", "orbit inclination (from r x v)"),
    },
    "AttGuidMsg": {
        "attitude_error_deg": Variable(1, "deg", "principal rotation angle of sigma_BR"),
        "rate_error_degps": Variable(1, "deg/s", "norm of omega_BR_B"),
    },
    "RWSpeedMsg": {
        "wheel_speeds_rpm": Variable(None, "rpm", "reaction wheel speeds"),
    },
    "CmdTorqueBodyMsg": {
        "torque_norm_Nm": Variable(1, "N*m", "norm of the commanded torque"),
    },
}


@dataclass(frozen=True)
class Port:
    msg_type: str
    description: str
    required: bool = False


@dataclass(frozen=True)
class ModuleType:
    name: str
    summary: str
    description: str
    category: str  # "dynamics" or "fsw"
    basilisk_class: str
    params: dict[str, Param]
    inputs: dict[str, Port] = field(default_factory=dict)
    outputs: dict[str, Port] = field(default_factory=dict)
    priority: int = 50

    def describe(self) -> dict[str, Any]:
        return {
            "type": self.name,
            "summary": self.summary,
            "description": self.description,
            "category": self.category,
            "basilisk_class": self.basilisk_class,
            "params": {k: p.schema() for k, p in self.params.items()},
            "inputs": {k: {"msg_type": p.msg_type, "required": p.required, "description": p.description}
                       for k, p in self.inputs.items()},
            "outputs": {k: {"msg_type": p.msg_type, "description": p.description,
                            "recordable_variables": sorted(MESSAGE_TYPES[p.msg_type])}
                        for k, p in self.outputs.items()},
        }

    def normalize_params(self, params: dict[str, Any] | None, base: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a complete, validated parameter dict.

        ``base`` holds the current values when updating; missing keys fall back to defaults.
        """
        params = params or {}
        if not isinstance(params, dict):
            raise ValidationError("params must be an object.")
        unknown = set(params) - set(self.params)
        if unknown:
            raise ValidationError(
                f"Unknown parameter(s) {sorted(unknown)} for module type '{self.name}'.",
                f"Valid parameters: {sorted(self.params)}. Call describe_module_type('{self.name}').",
            )
        out: dict[str, Any] = {}
        for key, param in self.params.items():
            where = f"{self.name}.{key}"
            if key in params:
                out[key] = normalize_value(param, params[key], where)
            elif base is not None and key in base:
                out[key] = base[key]
            elif param.required:
                raise ValidationError(f"Missing required parameter '{key}' for '{self.name}'.",
                                      f"{key}: {param.description}")
            else:
                out[key] = normalize_value(param, param.default, where) if param.default is not None else None
        self._cross_check(out)
        return out

    def _cross_check(self, p: dict[str, Any]) -> None:
        if self.name == "spacecraft":
            _check_inertia(p["inertia_kgm2"], "spacecraft.inertia_kgm2")
            has_orbit = p.get("orbit") is not None
            has_rv = p.get("position_m") is not None or p.get("velocity_mps") is not None
            if has_orbit and has_rv:
                raise ValidationError("Give either 'orbit' or 'position_m'+'velocity_mps', not both.")
            if has_rv and (p.get("position_m") is None or p.get("velocity_mps") is None):
                raise ValidationError("'position_m' and 'velocity_mps' must be given together.")
        if self.name == "gravity" and p["j2"] and p["central_body"] not in J2_MODELS:
            raise ValidationError(f"J2 is available for {sorted(J2_MODELS)} only, not '{p['central_body']}'.")
        if self.name == "reaction_wheels":
            allowed = RW_MODELS[p["model"]]["max_momentum_Nms"]
            if p["max_momentum_Nms"] not in allowed:
                raise ValidationError(
                    f"{p['model']} supports max_momentum_Nms in {list(allowed)}, got {p['max_momentum_Nms']}.")
            speeds = p.get("initial_speeds_rpm") or []
            if speeds and len(speeds) != len(p["spin_axes_B"]):
                raise ValidationError(
                    f"initial_speeds_rpm has {len(speeds)} entries but there are {len(p['spin_axes_B'])} wheels.")
        if self.name == "mrp_feedback" and p["Ki"] is not None and 0 <= p["Ki"] < 1e-12:
            raise ValidationError("Ki must be negative (integral feedback off) or positive.")


# --------------------------------------------------------------------------------------
# Physical constant tables (values from Basilisk 2.12 data files)
# --------------------------------------------------------------------------------------

CENTRAL_BODIES = ("earth", "moon", "mars", "sun", "venus", "jupiter")

#: J2-only zonal fields embedded from Basilisk's supportData/LocalGravData
#: (GGM03S-J2-only.txt for Earth, GGM2B-J2-only.txt for Mars). Normalized C20.
J2_MODELS: dict[str, dict[str, float]] = {
    "earth": {"radius_m": 6378136.3, "mu_m3ps2": 3.986004415e14, "c20_normalized": -4.841692638330e-04},
    "mars": {"radius_m": 3.397e6, "mu_m3ps2": 4.2828371901284001e13, "c20_normalized": -8.7450547081842009e-04},
}

RW_MODELS: dict[str, dict[str, Any]] = {
    "Honeywell_HR16": {"max_momentum_Nms": (50.0, 75.0, 100.0), "max_torque_Nm": 0.2, "max_speed_rpm": 6000.0},
    "Honeywell_HR14": {"max_momentum_Nms": (25.0, 50.0, 75.0), "max_torque_Nm": 0.2, "max_speed_rpm": 6000.0},
    "Honeywell_HR12": {"max_momentum_Nms": (12.0, 25.0, 50.0), "max_torque_Nm": 0.2, "max_speed_rpm": 6000.0},
}

INTEGRATORS = {
    "rk4": "svIntegratorRK4",
    "rk2": "svIntegratorRK2",
    "euler": "svIntegratorEuler",
    "rkf45": "svIntegratorRKF45",
    "rkf78": "svIntegratorRKF78",
}

IDENTITY_AXES = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

# --------------------------------------------------------------------------------------
# Module catalog
# --------------------------------------------------------------------------------------

MODULE_TYPES: dict[str, ModuleType] = {}


def _register(mt: ModuleType) -> None:
    MODULE_TYPES[mt.name] = mt


_register(ModuleType(
    name="spacecraft",
    summary="Rigid spacecraft hub with initial orbit and attitude.",
    description=(
        "Basilisk Spacecraft hub: mass, inertia, initial translational state (classical orbit "
        "elements, or inertial position/velocity) and initial attitude. Translational motion "
        "requires a 'gravity' module attached to it. Effectors (reaction wheels, external "
        "torque) attach to it via their 'attach_to' parameter."
    ),
    category="dynamics",
    basilisk_class="Basilisk.simulation.spacecraft.Spacecraft",
    params={
        "mass_kg": Param("float", "Hub mass.", "kg", default=750.0, minimum=0.0, exclusive_minimum=True,
                         maximum=1e7),
        "inertia_kgm2": Param("mat3", "Hub inertia about its center of mass, body frame.", "kg*m^2",
                              default=[[900.0, 0.0, 0.0], [0.0, 800.0, 0.0], [0.0, 0.0, 600.0]]),
        "orbit": Param("orbit", "Initial classical orbit elements relative to the attached gravity body.",
                       nullable=True),
        "position_m": Param("vec3", "Initial inertial position r_CN_N (alternative to 'orbit').", "m",
                            nullable=True),
        "velocity_mps": Param("vec3", "Initial inertial velocity v_CN_N (alternative to 'orbit').", "m/s",
                              nullable=True),
        "sigma_BN": Param("vec3", "Initial attitude as MRP of body relative to inertial.", "-",
                          default=[0.0, 0.0, 0.0], minimum=-10.0, maximum=10.0),
        "omega_BN_B_radps": Param("vec3", "Initial body angular rate in body frame.", "rad/s",
                                  default=[0.0, 0.0, 0.0], minimum=-10.0, maximum=10.0),
    },
    outputs={"state": Port("SCStatesMsg", "spacecraft state (position, velocity, attitude, rate)")},
    priority=100,
))

_register(ModuleType(
    name="gravity",
    summary="Central-body gravity (point mass, optional J2) attached to spacecraft.",
    description=(
        "Creates a gravity body with Basilisk's gravBodyFactory and attaches it to the listed "
        "spacecraft. 'j2' adds the J2 zonal harmonic for Earth (GGM03S) or Mars (GGM2B), "
        "identical to Basilisk's *-J2-only data files. The body is fixed at the inertial origin "
        "(no SPICE ephemerides)."
    ),
    category="dynamics",
    basilisk_class="Basilisk.utilities.simIncludeGravBody.gravBodyFactory",
    params={
        "central_body": Param("enum", "Central gravitating body.", default="earth", choices=CENTRAL_BODIES),
        "j2": Param("bool", "Include the J2 zonal harmonic (earth and mars only).", default=False),
        "attach_to": Param("refs", "Spacecraft modules this gravity field acts on.", required=True,
                           ref_type="spacecraft", max_items=16),
    },
    priority=0,
))

_register(ModuleType(
    name="ext_force_torque",
    summary="External body torque/force: constant values plus an optional commanded torque.",
    description=(
        "Basilisk ExtForceTorque effector. Applies a constant body-frame torque and force, plus the "
        "torque from its 'cmd_torque' input when connected (e.g. from mrp_feedback for an ideal "
        "torque actuator)."
    ),
    category="dynamics",
    basilisk_class="Basilisk.simulation.extForceTorque.ExtForceTorque",
    params={
        "torque_B_Nm": Param("vec3", "Constant torque about the center of mass, body frame.", "N*m",
                             default=[0.0, 0.0, 0.0], minimum=-1e4, maximum=1e4),
        "force_B_N": Param("vec3", "Constant force, body frame.", "N", default=[0.0, 0.0, 0.0],
                           minimum=-1e6, maximum=1e6),
        "attach_to": Param("ref", "Spacecraft module to attach to.", required=True, ref_type="spacecraft"),
    },
    inputs={"cmd_torque": Port("CmdTorqueBodyMsg", "commanded body torque (optional)")},
    priority=90,
))

_register(ModuleType(
    name="simple_nav",
    summary="Ideal navigation sensor: outputs attitude and translational navigation messages.",
    description="Basilisk SimpleNav without noise. Converts the spacecraft state into NavAtt/NavTrans messages.",
    category="dynamics",
    basilisk_class="Basilisk.simulation.simpleNav.SimpleNav",
    params={},
    inputs={"state": Port("SCStatesMsg", "spacecraft state", required=True)},
    outputs={
        "att": Port("NavAttMsg", "attitude navigation (sigma_BN, omega_BN_B)"),
        "trans": Port("NavTransMsg", "translational navigation (r_BN_N, v_BN_N)"),
    },
    priority=80,
))

_register(ModuleType(
    name="reaction_wheels",
    summary="Cluster of reaction wheels (Honeywell HR12/HR14/HR16) attached to a spacecraft.",
    description=(
        "Basilisk ReactionWheelStateEffector populated through simIncludeRW.rwFactory with balanced "
        "wheels. Outputs wheel speeds and the RW array configuration needed by mrp_feedback and "
        "rw_motor_torque."
    ),
    category="dynamics",
    basilisk_class="Basilisk.simulation.reactionWheelStateEffector.ReactionWheelStateEffector",
    params={
        "model": Param("enum", "Wheel model from Basilisk's RW library.", default="Honeywell_HR16",
                       choices=tuple(RW_MODELS)),
        "spin_axes_B": Param("vec3_list", "Spin axis of each wheel in the body frame (normalized by Basilisk).",
                             "-", default=IDENTITY_AXES, min_items=1, max_items=8),
        "max_momentum_Nms": Param("float", "Momentum storage option of the wheel model.", "N*m*s",
                                  default=50.0, minimum=0.0, exclusive_minimum=True),
        "initial_speeds_rpm": Param("float_list", "Initial wheel speeds, one per wheel (default all zero).",
                                    "rpm", default=[], minimum=-6000.0, maximum=6000.0, max_items=8),
        "attach_to": Param("ref", "Spacecraft module to attach to.", required=True, ref_type="spacecraft"),
    },
    inputs={"motor_torque": Port("ArrayMotorTorqueMsg", "motor torque command per wheel")},
    outputs={
        "speeds": Port("RWSpeedMsg", "wheel speeds"),
        "config": Port("RWArrayConfigMsg", "RW array configuration for flight software"),
    },
    priority=110,
))

_register(ModuleType(
    name="inertial3d",
    summary="Inertial pointing reference attitude.",
    description="Basilisk inertial3D guidance: a fixed reference attitude sigma_R0N relative to the inertial frame.",
    category="fsw",
    basilisk_class="Basilisk.fswAlgorithms.inertial3D.inertial3D",
    params={
        "sigma_R0N": Param("vec3", "Desired reference attitude MRP relative to inertial.", "-",
                           default=[0.0, 0.0, 0.0], minimum=-10.0, maximum=10.0),
    },
    outputs={"att_ref": Port("AttRefMsg", "reference attitude")},
    priority=100,
))

_register(ModuleType(
    name="hill_point",
    summary="Nadir (Hill/orbit frame) pointing reference attitude.",
    description="Basilisk hillPoint guidance: aligns the body with the Hill frame of the orbit (nadir pointing).",
    category="fsw",
    basilisk_class="Basilisk.fswAlgorithms.hillPoint.hillPoint",
    params={},
    inputs={"trans": Port("NavTransMsg", "translational navigation", required=True)},
    outputs={"att_ref": Port("AttRefMsg", "reference attitude")},
    priority=100,
))

_register(ModuleType(
    name="att_tracking_error",
    summary="Attitude tracking error between navigation and reference.",
    description="Basilisk attTrackingError: computes sigma_BR and omega_BR_B for the controller.",
    category="fsw",
    basilisk_class="Basilisk.fswAlgorithms.attTrackingError.attTrackingError",
    params={
        "sigma_R0R": Param("vec3", "Constant offset of the corrected reference relative to the input reference.",
                           "-", default=[0.0, 0.0, 0.0], minimum=-10.0, maximum=10.0),
    },
    inputs={
        "att": Port("NavAttMsg", "attitude navigation", required=True),
        "ref": Port("AttRefMsg", "reference attitude", required=True),
    },
    outputs={"guid": Port("AttGuidMsg", "attitude guidance (tracking error)")},
    priority=90,
))

_register(ModuleType(
    name="mrp_feedback",
    summary="MRP feedback attitude control law (PD, optional integral term).",
    description=(
        "Basilisk mrpFeedback. Gains: K (attitude, N*m), P (rate, N*m*s), Ki (integral, 1/s; negative "
        "disables). Needs the spacecraft inertia ('inertia_from'). Connect 'rw_params' and 'rw_speeds' "
        "together to include wheel gyroscopic terms."
    ),
    category="fsw",
    basilisk_class="Basilisk.fswAlgorithms.mrpFeedback.mrpFeedback",
    params={
        "K": Param("float", "Proportional attitude gain.", "N*m", default=3.5, minimum=0.0,
                   exclusive_minimum=True, maximum=1e6),
        "P": Param("float", "Rate gain.", "N*m*s", default=30.0, minimum=0.0, exclusive_minimum=True, maximum=1e6),
        "Ki": Param("float", "Integral gain; negative turns integral feedback off.", "1/s", default=-1.0,
                    minimum=-1.0, maximum=1e3),
        "integral_limit": Param("float", "Integral term limit (default 2/Ki*0.1 like the Basilisk examples).",
                                "N*m", nullable=True, minimum=-1e6, maximum=1e6),
        "inertia_from": Param("ref", "Spacecraft whose inertia the controller uses.", required=True,
                              ref_type="spacecraft"),
    },
    inputs={
        "guid": Port("AttGuidMsg", "attitude guidance", required=True),
        "rw_params": Port("RWArrayConfigMsg", "RW array configuration (optional, with rw_speeds)"),
        "rw_speeds": Port("RWSpeedMsg", "RW speeds (optional, with rw_params)"),
    },
    outputs={"cmd_torque": Port("CmdTorqueBodyMsg", "commanded body torque")},
    priority=80,
))

_register(ModuleType(
    name="rw_motor_torque",
    summary="Maps a commanded body torque onto reaction wheel motor torques.",
    description="Basilisk rwMotorTorque: projects the requested body torque onto the controllable axes.",
    category="fsw",
    basilisk_class="Basilisk.fswAlgorithms.rwMotorTorque.rwMotorTorque",
    params={
        "control_axes_B": Param("vec3_list", "Body axes on which torque is controlled.", "-",
                                default=IDENTITY_AXES, min_items=1, max_items=3),
    },
    inputs={
        "cmd_torque": Port("CmdTorqueBodyMsg", "commanded body torque", required=True),
        "rw_params": Port("RWArrayConfigMsg", "RW array configuration", required=True),
    },
    outputs={"motor_torque": Port("ArrayMotorTorqueMsg", "motor torque per wheel")},
    priority=70,
))


def get_module_type(name: str) -> ModuleType:
    if name not in MODULE_TYPES:
        raise ValidationError(f"Unknown module type {name!r}.", f"Available types: {sorted(MODULE_TYPES)}")
    return MODULE_TYPES[name]


def catalog() -> list[dict[str, Any]]:
    return [{"type": m.name, "category": m.category, "summary": m.summary,
             "inputs": {k: p.msg_type for k, p in m.inputs.items()},
             "outputs": {k: p.msg_type for k, p in m.outputs.items()}}
            for m in MODULE_TYPES.values()]
