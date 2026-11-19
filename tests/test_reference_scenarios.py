"""Physics validation: basilisk-mcp reproduces the official Basilisk example scenarios.

tests/data/reference.json is produced by tools/generate_reference.py from the unmodified
official examples. Here the same scenarios are built through the session API and run
through the isolated worker; the recorded data must match.
"""

import asyncio
import json
import math
from pathlib import Path

import numpy as np
import pytest

from basilisk_mcp import spec as S
from basilisk_mcp.manager import Manager
from basilisk_mcp.results import analyze_orbit

from .conftest import requires_basilisk

pytestmark = [requires_basilisk, pytest.mark.basilisk]

REFERENCE = json.loads((Path(__file__).parent / "data" / "reference.json").read_text())


def run_template(settings, template, duration, recorders, overrides=None):
    manager = Manager(settings)
    session = manager.create_session(template=template, overrides=overrides)
    for name in list(session.spec["recorders"]):
        manager.mutate(session.id, None, lambda sp, n=name: S.remove_recorder(sp, n))
    for name, (source, dt) in recorders.items():
        manager.mutate(session.id, None, lambda sp, n=name, s=source, d=dt: S.add_recorder(sp, n, s, d, settings))
    record = asyncio.run(manager.run(session.id, duration))
    assert record["status"] == "completed"
    return manager, session, manager.run_data(session.id, record["run_id"])[2]


def test_attitude_feedback_rw_matches_official_example(settings):
    ref = REFERENCE["attitude_feedback_rw"]
    _, _, data = run_template(settings, "attitude_feedback_rw", ref["duration_s"], {
        "guid": ("att_error.guid", 6.0), "speeds": ("rw.speeds", 6.0), "motor": ("rw_torque.motor_torque", 6.0)})
    np.testing.assert_allclose(data.time("guid"), ref["sample_times_s"], atol=1e-9)
    np.testing.assert_allclose(data.get("guid", "sigma_BR"), ref["sigma_BR"], rtol=1e-6, atol=1e-9)
    np.testing.assert_allclose(data.get("speeds", "wheelSpeeds"), ref["wheel_speeds_radps"], rtol=1e-6, atol=1e-6)
    np.testing.assert_allclose(data.get("motor", "motorTorque"), ref["motor_torque_Nm"], rtol=1e-6, atol=1e-9)


def test_basic_orbit_matches_official_example(settings):
    ref = REFERENCE["basic_orbit"]
    _, _, data = run_template(settings, "basic_orbit", ref["duration_s"], {"states": ("sc.state", 40.0)})
    np.testing.assert_allclose(data.time("states"), ref["sample_times_s"], atol=1e-9)
    np.testing.assert_allclose(data.get("states", "r_BN_N"), ref["r_BN_N"], rtol=1e-9, atol=1e-3)
    np.testing.assert_allclose(data.get("states", "v_BN_N"), ref["v_BN_N"], rtol=1e-9, atol=1e-6)


def test_point_mass_orbit_conserves_elements(settings):
    manager, session, data = run_template(settings, "basic_orbit", 86400.0, {"states": ("sc.state", 60.0)})
    result = analyze_orbit(data, "states", session.spec)
    assert abs(result["relative_energy_drift"]) < 1e-8
    assert abs(result["elements"]["raan_deg"]["change"]) < 1e-6
    assert abs(result["elements"]["semi_major_axis_km"]["change"]) < 1e-3


def test_j2_raan_drift_matches_analytic_rate(settings):
    overrides = {"earth": {"j2": True},
                 "sc": {"orbit": {"semi_major_axis_km": 7000.0, "eccentricity": 0.001, "inclination_deg": 45.0}}}
    manager, session, data = run_template(settings, "basic_orbit", 3 * 86400.0, {"states": ("sc.state", 60.0)},
                                          overrides)
    result = analyze_orbit(data, "states", session.spec)
    simulated = result["elements"]["raan_deg"]["mean_rate_deg_per_day"]
    analytic = result["j2_analytic_rates_deg_per_day"]["raan"]
    assert analytic < 0
    assert math.isclose(simulated, analytic, rel_tol=0.02), (simulated, analytic)


def test_nadir_pointing_converges(settings):
    manager = Manager(settings)
    session = manager.create_session(template="nadir_pointing_rw")
    record = asyncio.run(manager.run(session.id, 900.0))
    data = manager.run_data(session.id, record["run_id"])[2]
    error = data.get("att_error_log", "attitude_error_deg")[:, 0]
    assert error[0] > 30 and error[-1] < 0.5


def test_integrator_accuracy_ordering(settings):
    """Energy drift over one orbit must follow the integrators' order of accuracy."""
    manager = Manager(settings)
    drift = {}
    for integrator in ("euler", "rk2", "rk4", "rkf78"):
        session = manager.create_session(template="basic_orbit", simulation={"integrator": integrator})
        record = asyncio.run(manager.run(session.id, 5400.0))
        data = manager.run_data(session.id, record["run_id"])[2]
        drift[integrator] = abs(analyze_orbit(data, "states", session.spec)["relative_energy_drift"])
    assert drift["euler"] > drift["rk2"] > drift["rk4"] > drift["rkf78"], drift
