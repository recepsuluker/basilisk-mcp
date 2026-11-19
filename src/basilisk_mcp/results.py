"""Load run results and turn them into context-friendly answers.

Large time histories stay on disk (``results.npz``). Tools return down-sampled
series, statistics, physics analyses, comparisons and plots.
"""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .errors import NotFound, ValidationError
from .registry import DERIVED_VARIABLES, MESSAGE_TYPES, RW_MODELS

RPM_PER_RADPS = 60.0 / (2.0 * math.pi)
NEAR_CIRCULAR_ECCENTRICITY = 0.01


class RunData:
    """Results of one run: ``summary.json`` plus lazily loaded arrays."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.summary = json.loads((run_dir / "summary.json").read_text())
        with np.load(run_dir / "results.npz", allow_pickle=False) as data:
            self.arrays = {k: np.array(data[k]) for k in data.files}

    # ---- access -----------------------------------------------------------------------

    def recorders(self) -> dict[str, Any]:
        return self.summary["recorders"]

    def recorder(self, name: str) -> dict[str, Any]:
        if name not in self.recorders():
            raise NotFound("Recorder", name, sorted(self.recorders()))
        return self.recorders()[name]

    def time(self, rec: str) -> np.ndarray:
        self.recorder(rec)
        return self.arrays[f"{rec}__time_s"]

    def variables(self, rec: str) -> dict[str, dict[str, Any]]:
        meta = self.recorder(rec)
        msg_type = meta["msg_type"]
        out = {}
        for v, width in meta["variables"].items():
            vdef = MESSAGE_TYPES[msg_type][v]
            out[v] = {"width": width, "unit": vdef.unit, "description": vdef.description, "derived": False}
        for v, vdef in DERIVED_VARIABLES.get(msg_type, {}).items():
            width = vdef.width or next(iter(meta["variables"].values()))
            out[v] = {"width": width, "unit": vdef.unit, "description": vdef.description, "derived": True}
        return out

    def get(self, rec: str, variable: str) -> np.ndarray:
        """Return a (samples, width) array for a raw or derived variable."""
        meta = self.recorder(rec)
        if variable in meta["variables"]:
            return self.arrays[f"{rec}__{variable}"]
        msg_type = meta["msg_type"]
        if variable not in DERIVED_VARIABLES.get(msg_type, {}):
            raise NotFound(f"Variable of recorder '{rec}'", variable, sorted(self.variables(rec)))
        a = self.arrays
        if variable == "radius_km":
            return (np.linalg.norm(a[f"{rec}__r_BN_N"], axis=1) / 1000.0)[:, None]
        if variable == "speed_kmps":
            return (np.linalg.norm(a[f"{rec}__v_BN_N"], axis=1) / 1000.0)[:, None]
        if variable in ("raan_deg", "inclination_deg"):
            h = np.cross(a[f"{rec}__r_BN_N"], a[f"{rec}__v_BN_N"])
            if variable == "inclination_deg":
                return np.degrees(np.arccos(np.clip(h[:, 2] / np.linalg.norm(h, axis=1), -1.0, 1.0)))[:, None]
            return _unwrap_deg(np.degrees(np.arctan2(h[:, 0], -h[:, 1])) % 360.0)[:, None]
        if variable == "attitude_angle_deg":
            return mrp_angle_deg(a[f"{rec}__sigma_BN"])[:, None]
        if variable == "rate_norm_degps":
            return np.degrees(np.linalg.norm(a[f"{rec}__omega_BN_B"], axis=1))[:, None]
        if variable == "attitude_error_deg":
            return mrp_angle_deg(a[f"{rec}__sigma_BR"])[:, None]
        if variable == "rate_error_degps":
            return np.degrees(np.linalg.norm(a[f"{rec}__omega_BR_B"], axis=1))[:, None]
        if variable == "wheel_speeds_rpm":
            return a[f"{rec}__wheelSpeeds"] * RPM_PER_RADPS
        if variable == "torque_norm_Nm":
            return np.linalg.norm(a[f"{rec}__torqueRequestBody"], axis=1)[:, None]
        raise AssertionError(variable)  # pragma: no cover

    def unit(self, rec: str, variable: str) -> str:
        return self.variables(rec)[variable]["unit"]


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def mrp_angle_deg(sigma: np.ndarray) -> np.ndarray:
    """Principal rotation angle of MRPs: phi = 4 * atan(|sigma|)."""
    return np.degrees(4.0 * np.arctan(np.linalg.norm(sigma, axis=1)))


def parse_series(expr: Any) -> tuple[str, str, int | None]:
    """'rec.var' or 'rec.var[i]' -> (rec, var, index)."""
    if not isinstance(expr, str) or expr.count(".") != 1 or len(expr) > 80:
        raise ValidationError(f"Series must look like 'recorder.variable' or 'recorder.variable[i]', got {expr!r}.",
                              "Example: 'att_error_log.attitude_error_deg' or 'states.r_BN_N[0]'.")
    rec, rest = expr.split(".")
    index = None
    if rest.endswith("]") and "[" in rest:
        rest, idx = rest[:-1].split("[", 1)
        if not idx.isdigit():
            raise ValidationError(f"Invalid component index in {expr!r}.")
        index = int(idx)
    return rec, rest, index


def select(data: RunData, expr: str) -> tuple[np.ndarray, np.ndarray, list[str], str]:
    """Resolve a series expression to (time, values (n, k), column labels, unit)."""
    rec, variable, index = parse_series(expr)
    values = data.get(rec, variable)
    labels = [f"{rec}.{variable}[{i}]" for i in range(values.shape[1])] if values.shape[1] > 1 \
        else [f"{rec}.{variable}"]
    if index is not None:
        if index >= values.shape[1]:
            raise ValidationError(f"{rec}.{variable} has {values.shape[1]} component(s); index {index} is out of range.")
        values, labels = values[:, [index]], [labels[index]]
    return data.time(rec), values, labels, data.unit(rec, variable)


def downsample_indices(n: int, max_points: int) -> np.ndarray:
    if n <= max_points:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, max_points).round().astype(int))


def _round(x: np.ndarray, digits: int = 10) -> list:
    return [float(f"{v:.{digits}g}") for v in np.asarray(x).ravel()]


def stats(values: np.ndarray) -> dict[str, Any]:
    return {
        "initial": _round(values[0]),
        "final": _round(values[-1]),
        "min": _round(values.min(axis=0)),
        "max": _round(values.max(axis=0)),
        "max_abs": _round(np.abs(values).max(axis=0)),
    }


def run_overview(data: RunData) -> dict[str, Any]:
    """Summary of every recorded variable (initial/final/min/max)."""
    out = {}
    for rec, meta in data.recorders().items():
        t = data.time(rec)
        variables = {}
        for v in data.variables(rec):
            variables[v] = {"unit": data.unit(rec, v), **stats(data.get(rec, v))}
        out[rec] = {"source": meta["source"], "msg_type": meta["msg_type"], "samples": int(len(t)),
                    "t_start_s": float(t[0]) if len(t) else None, "t_end_s": float(t[-1]) if len(t) else None,
                    "variables": variables}
    return out


# --------------------------------------------------------------------------------------
# Query
# --------------------------------------------------------------------------------------


def query(data: RunData, recorder: str, variables: list[str] | None, max_points: int,
          t_start: float | None, t_end: float | None) -> dict[str, Any]:
    t = data.time(recorder)
    available = data.variables(recorder)
    names = variables or [v for v, info in available.items() if not info["derived"]]
    for v in names:
        if v not in available:
            raise NotFound(f"Variable of recorder '{recorder}'", v, sorted(available))
    mask = np.ones(len(t), dtype=bool)
    if t_start is not None:
        mask &= t >= t_start
    if t_end is not None:
        mask &= t <= t_end
    idx_all = np.nonzero(mask)[0]
    idx = idx_all[downsample_indices(len(idx_all), max_points)]
    out = {
        "recorder": recorder,
        "samples_in_window": int(len(idx_all)),
        "points_returned": int(len(idx)),
        "downsampled": bool(len(idx) < len(idx_all)),
        "time_s": _round(t[idx], 12),
        "variables": {},
    }
    for v in names:
        values = data.get(recorder, v)[idx]
        out["variables"][v] = {
            "unit": available[v]["unit"],
            "values": [_round(row) for row in values] if values.shape[1] > 1 else _round(values[:, 0]),
        }
    return out


# --------------------------------------------------------------------------------------
# Analyses
# --------------------------------------------------------------------------------------


def rv2elements(mu: float, r: np.ndarray, v: np.ndarray) -> dict[str, np.ndarray]:
    """Vectorized classical orbit elements (elliptic orbits). Angles in degrees."""
    rn = np.linalg.norm(r, axis=1)
    vn2 = np.einsum("ij,ij->i", v, v)
    h = np.cross(r, v)
    hn = np.linalg.norm(h, axis=1)
    energy = vn2 / 2.0 - mu / rn
    a = -mu / (2.0 * energy)
    e_vec = (np.cross(v, h) / mu) - r / rn[:, None]
    e = np.linalg.norm(e_vec, axis=1)
    inc = np.degrees(np.arccos(np.clip(h[:, 2] / hn, -1.0, 1.0)))
    node = np.cross(np.array([0.0, 0.0, 1.0]), h)
    nn = np.linalg.norm(node, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        raan = np.degrees(np.arctan2(node[:, 1], node[:, 0])) % 360.0
        argp = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", node, e_vec) / (nn * e), -1, 1)))
        argp = np.where(e_vec[:, 2] < 0, 360.0 - argp, argp)
        nu = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", e_vec, r) / (e * rn), -1, 1)))
        nu = np.where(np.einsum("ij,ij->i", r, v) < 0, 360.0 - nu, nu)
        # Argument of latitude u = argp + nu, well defined for circular orbits.
        u = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", node, r) / (nn * rn), -1, 1)))
        u = np.where(r[:, 2] < 0, 360.0 - u, u)
    return {"semi_major_axis_km": a / 1000.0, "eccentricity": e, "inclination_deg": inc, "raan_deg": raan,
            "arg_periapsis_deg": argp, "true_anomaly_deg": nu, "arg_latitude_deg": u,
            "specific_energy_J_per_kg": energy}


def _unwrap_deg(x: np.ndarray) -> np.ndarray:
    return np.degrees(np.unwrap(np.radians(x)))


def analyze_orbit(data: RunData, recorder: str, spec: dict[str, Any]) -> dict[str, Any]:
    meta = data.recorder(recorder)
    if meta["msg_type"] not in ("SCStatesMsg", "NavTransMsg"):
        raise ValidationError(f"Orbit analysis needs a recorder of SCStatesMsg or NavTransMsg; "
                              f"'{recorder}' records {meta['msg_type']}.")
    module = meta["source"].split(".")[0]
    sc = module
    if spec["modules"].get(module, {}).get("type") == "simple_nav":
        src = next((c["source"] for c in spec["connections"] if c["target"] == f"{module}.state"), None)
        sc = src.split(".")[0] if src else module
    grav_name = data.summary.get("spacecraft_gravity", {}).get(sc)
    if not grav_name:
        raise ValidationError(f"Spacecraft '{sc}' has no gravity module, so orbit elements are undefined.")
    grav = data.summary["gravity"][grav_name]
    mu, radius = grav["mu_m3ps2"], grav["radius_m"]
    t = data.time(recorder)
    r, v = data.arrays[f"{recorder}__r_BN_N"], data.arrays[f"{recorder}__v_BN_N"]
    el = rv2elements(mu, r, v)
    if np.any(el["eccentricity"] >= 1.0):
        raise ValidationError("The trajectory is not elliptic; classical elements are not reported.")
    span_days = (t[-1] - t[0]) / 86400.0 if len(t) > 1 else 0.0
    near_circular = float(np.mean(el["eccentricity"])) < NEAR_CIRCULAR_ECCENTRICITY
    circular_note = (f"Near-circular orbit (mean e < {NEAR_CIRCULAR_ECCENTRICITY}): periapsis is ill-defined, so this "
                     "element is not meaningful; use arg_latitude_deg.")
    elements = {}
    for key, series in el.items():
        if key == "specific_energy_J_per_kg":
            continue
        angular = key in ("raan_deg", "arg_periapsis_deg", "true_anomaly_deg", "arg_latitude_deg")
        s = _unwrap_deg(series) if angular else series
        entry: dict[str, Any] = {"initial": float(s[0]), "final": float(s[-1]), "min": float(s.min()),
                                 "max": float(s.max()), "change": float(s[-1] - s[0])}
        if near_circular and key in ("arg_periapsis_deg", "true_anomaly_deg"):
            entry = {"initial": float(s[0]), "note": circular_note}
        elif span_days > 0 and key in ("raan_deg", "arg_periapsis_deg"):
            slope = np.polyfit(t / 86400.0, s, 1)[0] if len(t) > 2 else (s[-1] - s[0]) / span_days
            entry["mean_rate_deg_per_day"] = float(slope)
        elements[key] = entry
    energy = el["specific_energy_J_per_kg"]
    a0 = el["semi_major_axis_km"][0] * 1000.0
    rn = np.linalg.norm(r, axis=1)
    j2_grav = next((m for m in spec["modules"].values()
                    if m["type"] == "gravity" and sc in m["params"]["attach_to"]), None)
    has_j2 = bool(j2_grav and j2_grav["params"]["j2"])
    energy_change = float((energy[-1] - energy[0]) / abs(energy[0]))
    out = {
        "recorder": recorder,
        "spacecraft": sc,
        "central_body": grav["central_body"],
        "mu_m3ps2": mu,
        "time_span_s": float(t[-1] - t[0]),
        "orbital_period_s": float(2.0 * math.pi * math.sqrt(a0 ** 3 / mu)),
        "periapsis_altitude_km": float((rn.min() - radius) / 1000.0),
        "apoapsis_altitude_km": float((rn.max() - radius) / 1000.0),
        "elements": elements,
        "near_circular": near_circular,
    }
    if has_j2:
        out["keplerian_energy_relative_change"] = energy_change
        out["note"] = ("Osculating elements. The Keplerian energy v^2/2 - mu/r is not conserved under J2 (the J2 "
                       "potential is omitted from it), so its change is physical, not integration error. RAAN and "
                       "argument of periapsis drift secularly.")
    else:
        out["relative_energy_drift"] = energy_change
        out["note"] = ("Osculating elements. With point-mass gravity a, e, i, RAAN and argp stay constant; the "
                       "relative energy drift measures integration error.")
    if has_j2:
        from .registry import J2_MODELS

        model = J2_MODELS[j2_grav["params"]["central_body"]]
        j2 = -model["c20_normalized"] * math.sqrt(5.0)
        a = float(np.mean(el["semi_major_axis_km"])) * 1000.0
        e = float(np.mean(el["eccentricity"]))
        i = math.radians(float(np.mean(el["inclination_deg"])))
        n = math.sqrt(mu / a ** 3)
        p = a * (1.0 - e * e)
        factor = 1.5 * n * j2 * (model["radius_m"] / p) ** 2
        out["j2_analytic_rates_deg_per_day"] = {
            "raan": math.degrees(-factor * math.cos(i)) * 86400.0,
            "arg_periapsis": math.degrees(factor * (2.0 - 2.5 * math.sin(i) ** 2)) * 86400.0,
            "note": "First-order secular rates from mean elements; compare with mean_rate_deg_per_day.",
        }
    return out


def analyze_attitude(data: RunData, recorder: str, threshold_deg: float) -> dict[str, Any]:
    meta = data.recorder(recorder)
    if meta["msg_type"] != "AttGuidMsg":
        raise ValidationError(f"Attitude analysis needs an AttGuidMsg recorder (att_tracking_error.guid); "
                              f"'{recorder}' records {meta['msg_type']}.")
    t = data.time(recorder)
    err = mrp_angle_deg(data.arrays[f"{recorder}__sigma_BR"])
    rate = np.degrees(np.linalg.norm(data.arrays[f"{recorder}__omega_BR_B"], axis=1))
    above = np.nonzero(err > threshold_deg)[0]
    if len(above) == 0:
        settle = float(t[0])
    elif above[-1] == len(err) - 1:
        settle = None
    else:
        settle = float(t[above[-1] + 1])
    return {
        "recorder": recorder,
        "threshold_deg": threshold_deg,
        "initial_error_deg": float(err[0]),
        "final_error_deg": float(err[-1]),
        "max_error_deg": float(err.max()),
        "final_rate_error_degps": float(rate[-1]),
        "settling_time_s": settle,
        "settled": settle is not None,
        "error_at_fraction_of_run_deg": {f"{int(f * 100)}%": float(np.interp(t[0] + f * (t[-1] - t[0]), t, err))
                                         for f in (0.1, 0.25, 0.5, 0.75)},
    }


def analyze_actuators(data: RunData, spec: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for rec, meta in data.recorders().items():
        mt = meta["msg_type"]
        if mt == "RWSpeedMsg":
            rpm = data.get(rec, "wheel_speeds_rpm")
            module = spec["modules"].get(meta["source"].split(".")[0], {})
            model = module.get("params", {}).get("model")
            entry = {"max_abs_speed_rpm": _round(np.abs(rpm).max(axis=0), 6),
                     "final_speed_rpm": _round(rpm[-1], 6)}
            if model in RW_MODELS:
                limit = RW_MODELS[model]["max_speed_rpm"]
                entry["speed_limit_rpm"] = limit
                entry["peak_fraction_of_limit"] = float(np.abs(rpm).max() / limit)
            out[rec] = entry
        elif mt == "ArrayMotorTorqueMsg":
            tq = data.get(rec, "motorTorque")
            entry = {"max_abs_torque_Nm": _round(np.abs(tq).max(axis=0), 6),
                     "rms_torque_Nm": _round(np.sqrt((tq ** 2).mean(axis=0)), 6)}
            out[rec] = entry
        elif mt == "CmdTorqueBodyMsg":
            tq = data.get(rec, "torqueRequestBody")
            dt = np.diff(data.time(rec))
            impulse = np.sum(np.abs(tq[:-1]) * dt[:, None], axis=0) if len(dt) else np.zeros(3)
            out[rec] = {"max_abs_torque_Nm": _round(np.abs(tq).max(axis=0), 6),
                        "angular_impulse_abs_Nms": _round(impulse, 6)}
    if not out:
        raise ValidationError("No actuator recorders (RWSpeedMsg, ArrayMotorTorqueMsg, CmdTorqueBodyMsg) in this run.")
    return out


# --------------------------------------------------------------------------------------
# Comparison, export and plotting
# --------------------------------------------------------------------------------------


def compare(runs: dict[str, RunData], series: str) -> dict[str, Any]:
    resolved = {rid: select(d, series) for rid, d in runs.items()}
    base_id = next(iter(resolved))
    t0, v0, labels, unit = resolved[base_id]
    out: dict[str, Any] = {"series": series, "unit": unit, "baseline_run": base_id, "runs": {}}
    for rid, (t, v, _, _) in resolved.items():
        entry = {"final": _round(v[-1]), "max_abs": _round(np.abs(v).max(axis=0)), "t_end_s": float(t[-1])}
        if rid != base_id:
            lo, hi = max(t0[0], t[0]), min(t0[-1], t[-1])
            if hi > lo:
                grid = np.linspace(lo, hi, 500)
                diff = np.column_stack([np.interp(grid, t, v[:, k]) - np.interp(grid, t0, v0[:, k])
                                        for k in range(min(v.shape[1], v0.shape[1]))])
                entry["difference_vs_baseline"] = {
                    "rms": _round(np.sqrt((diff ** 2).mean(axis=0))),
                    "max_abs": _round(np.abs(diff).max(axis=0)),
                    "final": _round(diff[-1]),
                    "overlap_s": [float(lo), float(hi)],
                }
        out["runs"][rid] = entry
    return out


def export_csv(data: RunData, recorder: str, path: Path) -> dict[str, Any]:
    t = data.time(recorder)
    meta = data.recorder(recorder)
    columns, header = [t[:, None]], ["time_s"]
    for v, width in meta["variables"].items():
        columns.append(data.arrays[f"{recorder}__{v}"])
        header += [v] if width == 1 else [f"{v}[{i}]" for i in range(width)]
    table = np.hstack(columns)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(table.tolist())
    return {"path": str(path), "rows": int(table.shape[0]), "columns": header}


def plot(runs: dict[str, RunData], series: list[str], title: str | None, path: Path,
         x_unit: str = "s") -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    scale = {"s": 1.0, "min": 60.0, "h": 3600.0, "day": 86400.0}[x_unit]
    units: dict[str, list[str]] = {}
    for s in series:
        _, _, _, unit = select(next(iter(runs.values())), s)
        units.setdefault(unit, []).append(s)
    fig, axes = plt.subplots(len(units), 1, figsize=(9, 3.2 * len(units)), sharex=True, squeeze=False)
    for ax, (unit, group) in zip(axes[:, 0], units.items(), strict=True):
        for rid, data in runs.items():
            for s in group:
                t, v, labels, _ = select(data, s)
                for k in range(v.shape[1]):
                    label = labels[k] if len(runs) == 1 else f"{rid}: {labels[k]}"
                    ax.plot(t / scale, v[:, k], label=label, linewidth=1.4)
        ax.set_ylabel(f"[{unit}]")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8, loc="best")
    axes[-1, 0].set_xlabel(f"time [{x_unit}]")
    if title:
        fig.suptitle(title[:120])
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110)
    plt.close(fig)
    png = buffer.getvalue()
    path.write_bytes(png)
    return png
