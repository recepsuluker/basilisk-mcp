#!/usr/bin/env python3
"""Drive basilisk-mcp exactly like Claude would: over stdio, through MCP tool calls.

The script launches ``basilisk-mcp serve`` as a subprocess, runs three example
studies, saves the plots to ``docs/images/`` and writes ``examples/RESULTS.md``.

    python examples/demo_client.py

Requires basilisk-mcp and Basilisk (``pip install -e ".[basilisk]"``).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "docs" / "images"


def data(result):
    if result.is_error:
        raise RuntimeError(result.content[0].text)
    return result.structured_content


async def call(client, tool, **arguments):
    return data(await client.call_tool(tool, arguments))


async def plot(client, filename, **arguments):
    result = await client.call_tool("plot_results", arguments)
    if result.is_error:
        raise RuntimeError(result.content[0].text)
    image = next(c for c in result.content if c.type == "image")
    (IMAGES / filename).write_bytes(base64.b64decode(image.data))
    return f"../docs/images/{filename}"


async def orbit_study(client, report):
    """Example 1: orbit propagation, point-mass vs. J2."""
    session = await call(client, "create_session", name="LEO J2 study", template="basic_orbit", overrides={
        "sc": {"orbit": {"altitude_km": 700.0, "eccentricity": 0.001, "inclination_deg": 98.19,
                         "raan_deg": 30.0, "arg_periapsis_deg": 0.0, "true_anomaly_deg": 0.0}}})
    sid = session["session_id"]
    await call(client, "remove_recorder", session_id=sid, name="states")
    await call(client, "add_recorder", session_id=sid, name="states", source="sc.state", sample_dt_s=60.0)
    duration = 3 * 86400.0  # [s]
    await call(client, "run_simulation", session_id=sid, duration_s=duration, label="point mass")
    await call(client, "set_params", session_id=sid, name="earth", params={"j2": True})
    await call(client, "run_simulation", session_id=sid, duration_s=duration, label="J2")
    point = (await call(client, "analyze_run", session_id=sid, analysis="orbit", run_id="r001"))["result"]
    j2 = (await call(client, "analyze_run", session_id=sid, analysis="orbit", run_id="r002"))["result"]
    image = await plot(client, "orbit_j2_raan.png", session_id=sid, run_ids=["r001", "r002"],
                       series=["states.raan_deg", "states.radius_km"], time_unit="h",
                       title="700 km sun-synchronous orbit: point-mass vs. J2")
    report += [
        "## 1. Orbit propagation: point-mass vs. J2",
        "",
        "A 700 km, 98.19 deg sun-synchronous orbit propagated for three days, first with point-mass "
        "Earth gravity, then with the J2 zonal harmonic (`set_params(earth, {j2: true})`).",
        "",
        "| Quantity | Point mass | J2 | J2 analytic |",
        "|---|---|---|---|",
        f"| RAAN rate [deg/day] | {point['elements']['raan_deg'].get('mean_rate_deg_per_day', 0):.5f} "
        f"| {j2['elements']['raan_deg']['mean_rate_deg_per_day']:.5f} "
        f"| {j2['j2_analytic_rates_deg_per_day']['raan']:.5f} |",
        f"| Inclination change [deg] | {point['elements']['inclination_deg']['change']:.2e} "
        f"| {j2['elements']['inclination_deg']['change']:.2e} | 0 (secular) |",
        f"| Semi-major axis range [km] | {point['elements']['semi_major_axis_km']['max'] - point['elements']['semi_major_axis_km']['min']:.4f} "
        f"| {j2['elements']['semi_major_axis_km']['max'] - j2['elements']['semi_major_axis_km']['min']:.3f} | short-period only |",
        f"| Relative energy drift (point mass) [-] | {point['relative_energy_drift']:.2e} | | |",
        f"| Orbital period [min] | {point['orbital_period_s'] / 60:.3f} | {j2['orbital_period_s'] / 60:.3f} | |",
        "",
        "A sun-synchronous orbit needs a RAAN rate of +0.9856 deg/day (360 deg per year); the J2 run "
        "reproduces the analytic rate, while point-mass gravity keeps the plane fixed. The osculating "
        "semi-major axis oscillates by about 18 km peak to peak under J2, consistent with the short-period "
        "amplitude 1.5*J2*R^2/a = 9.3 km, but does not drift. With "
        "e = 0.001 the periapsis is ill-defined, so the analysis reports the argument of latitude instead "
        "of the argument of periapsis.",
        "",
        f"![orbit]({image})",
        "",
    ]


async def attitude_study(client, report):
    """Example 2: reaction-wheel attitude control and gain tuning."""
    session = await call(client, "create_session", name="RW attitude", template="attitude_feedback_rw")
    sid = session["session_id"]
    runs = {"r001": "K=3.5, P=30 (Basilisk example)", "r002": "K=7, P=40", "r003": "K=12, P=60"}
    await call(client, "run_simulation", session_id=sid, duration_s=600.0, label=runs["r001"])
    await call(client, "set_params", session_id=sid, name="control", params={"K": 7.0, "P": 40.0})
    await call(client, "run_simulation", session_id=sid, duration_s=600.0, label=runs["r002"])
    await call(client, "set_params", session_id=sid, name="control", params={"K": 12.0, "P": 60.0})
    await call(client, "run_simulation", session_id=sid, duration_s=600.0, label=runs["r003"])
    rows = []
    for rid, label in runs.items():
        att = (await call(client, "analyze_run", session_id=sid, analysis="attitude", run_id=rid))["result"]
        act = (await call(client, "analyze_run", session_id=sid, analysis="actuators", run_id=rid))["result"]
        settle = f"{att['settling_time_s']:.0f}" if att["settled"] else "not settled"
        rows.append(f"| {label} | {att['initial_error_deg']:.1f} | {settle} | {att['final_error_deg']:.4f} "
                    f"| {max(act['rw_speed_log']['max_abs_speed_rpm']):.0f} "
                    f"| {max(act['rw_torque_log']['max_abs_torque_Nm']):.3f} |")
    image1 = await plot(client, "attitude_rw_error.png", session_id=sid, run_ids=list(runs),
                        series=["att_error_log.attitude_error_deg"], title="Attitude error for three gain sets")
    image2 = await plot(client, "attitude_rw_wheels.png", session_id=sid, run_ids=["r001"],
                        series=["rw_speed_log.wheel_speeds_rpm", "rw_torque_log.motorTorque"],
                        title="Wheel speeds and motor torques (Basilisk example gains)")
    report += [
        "## 2. Attitude control with reaction wheels",
        "",
        "The `attitude_feedback_rw` template reproduces Basilisk's `scenarioAttitudeFeedbackRW.py` "
        "(three Honeywell HR16 wheels, MRP feedback, inertial pointing). The gains were then changed with "
        "`set_params` and the runs compared.",
        "",
        "| Gains | Initial error [deg] | Settling time to 1 deg [s] | Final error [deg] | Peak wheel speed [rpm] | Peak motor torque [N*m] |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
        "Peak motor torques above the HR16's 0.2 N*m limit are saturated by the wheel model, so higher gains "
        "give diminishing returns.",
        "",
        f"![attitude error]({image1})",
        "",
        f"![wheels]({image2})",
        "",
    ]


async def nadir_study(client, report):
    """Example 3: nadir pointing in LEO."""
    session = await call(client, "create_session", name="Nadir pointing", template="nadir_pointing_rw")
    sid = session["session_id"]
    await call(client, "run_simulation", session_id=sid, duration_s=900.0)
    att = (await call(client, "analyze_run", session_id=sid, analysis="attitude", threshold_deg=0.5))["result"]
    image = await plot(client, "nadir_pointing.png", session_id=sid,
                       series=["att_error_log.attitude_error_deg", "rw_speed_log.wheel_speeds_rpm"],
                       title="Nadir (Hill frame) pointing from a tumble, 500 km LEO")
    script = await call(client, "export_script", session_id=sid, duration_s=900.0)
    report += [
        "## 3. Nadir pointing from a tumble",
        "",
        "`nadir_pointing_rw`: `hill_point` guidance in a 500 km, 97.4 deg orbit with J2, starting from the "
        "same tumbling state as the Basilisk example.",
        "",
        f"* Initial error: {att['initial_error_deg']:.1f} deg; final error: {att['final_error_deg']:.4f} deg",
        f"* Settling time below 0.5 deg: {att['settling_time_s']:.0f} s",
        f"* `export_script` returned a {script['lines']}-line standalone Basilisk script for this run.",
        "",
        f"![nadir]({image})",
        "",
    ]


async def main() -> None:
    IMAGES.mkdir(parents=True, exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix="basilisk-mcp-demo-"))
    exe = shutil.which("basilisk-mcp")
    command, args = (exe, []) if exe else (sys.executable, ["-m", "basilisk_mcp"])
    params = StdioServerParameters(command=command, args=[*args, "--data-dir", str(data_dir), "serve"],
                                   env={**os.environ})
    report = [
        "# Example results",
        "",
        "Generated by `examples/demo_client.py`, which talks to `basilisk-mcp serve` over stdio using only MCP "
        "tool calls, the same way Claude does.",
        "",
    ]
    async with Client(params) as client:
        info = await call(client, "server_info")
        report.insert(4, f"Basilisk {info['basilisk'].get('basilisk')} · basilisk-mcp {info['version']}\n")
        await orbit_study(client, report)
        await attitude_study(client, report)
        await nadir_study(client, report)
    (ROOT / "examples" / "RESULTS.md").write_text("\n".join(report))
    print(json.dumps({"results": "examples/RESULTS.md", "images": sorted(p.name for p in IMAGES.glob("*.png"))}))


if __name__ == "__main__":
    asyncio.run(main())
