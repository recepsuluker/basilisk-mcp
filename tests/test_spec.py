import pytest

from basilisk_mcp import spec as S
from basilisk_mcp.errors import Conflict, LimitExceeded, NotFound, ValidationError
from basilisk_mcp.templates import TEMPLATES


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_templates_are_valid(name, make_spec, settings):
    spec = make_spec(name)
    assert S.validate_spec(spec, settings, TEMPLATES[name]["suggested_duration_s"]) == []


def test_connect_type_checking(make_spec):
    spec = make_spec("attitude_feedback_rw")
    S.disconnect(spec, "att_error.ref")
    with pytest.raises(ValidationError, match="mismatch") as exc:
        S.connect(spec, "nav.att", "att_error.ref")
    assert "guidance.att_ref" in exc.value.hint
    with pytest.raises(Conflict):
        S.connect(spec, "sc.state", "nav.state")
    with pytest.raises(ValidationError, match="no output port"):
        S.connect(spec, "nav.state", "att_error.ref")
    with pytest.raises(NotFound):
        S.connect(spec, "ghost.att_ref", "att_error.ref")


def test_missing_required_input_reported(make_spec, settings):
    spec = make_spec("attitude_feedback_rw")
    S.disconnect(spec, "control.guid")
    problems = S.validate_spec(spec, settings)
    assert any("control.guid" in p and "att_error.guid" in p for p in problems)


def test_remove_module_cascades(make_spec, settings):
    spec = make_spec("attitude_feedback_rw")
    removed = S.remove_module(spec, "rw")
    assert {"source": "rw.speeds", "target": "control.rw_speeds"} in removed["removed_connections"]
    assert "rw_speed_log" in removed["removed_recorders"]
    problems = S.validate_spec(spec, settings)
    assert any("rw_torque.rw_params" in p for p in problems)


def test_orbit_without_gravity_and_inside_planet(make_spec, settings):
    spec = make_spec("basic_orbit")
    S.remove_module(spec, "earth")
    assert any("no 'gravity'" in p for p in S.validate_spec(spec, settings))
    spec = make_spec("basic_orbit")
    S.set_params(spec, "sc", {"orbit": {"semi_major_axis_km": 6000}})
    assert any("inside earth" in p for p in S.validate_spec(spec, settings))


def test_wrong_reference_type(make_spec, settings):
    spec = make_spec("attitude_feedback_rw")
    S.set_params(spec, "control", {"inertia_from": "nav"})
    assert any("must refer to a 'spacecraft'" in p for p in S.validate_spec(spec, settings))


def test_recorder_rules(make_spec, settings):
    spec = make_spec("basic_orbit")
    with pytest.raises(ValidationError, match="configuration message"):
        S.add_module(spec, "rw", "reaction_wheels", {"attach_to": "sc"}, settings)
        S.add_recorder(spec, "cfg", "rw.config", 10, settings)
    S.add_recorder(spec, "fast", "sc.state", 15, settings)
    assert any("integer multiple" in p for p in S.validate_spec(spec, settings))


def test_duration_limits(make_spec, settings):
    spec = make_spec("basic_orbit")
    problems = S.validate_spec(spec, settings, 1e12)
    assert any("exceeds the limit" in p for p in problems)
    settings.max_steps = 10
    assert any("max_steps" in p for p in S.validate_spec(spec, settings, 1000))


def test_module_limit(make_spec, settings):
    settings.max_modules = 2
    spec = make_spec()
    S.add_module(spec, "a", "simple_nav", {}, settings)
    S.add_module(spec, "b", "simple_nav", {}, settings)
    with pytest.raises(LimitExceeded):
        S.add_module(spec, "c", "simple_nav", {}, settings)


def test_unknown_module_type(make_spec, settings):
    with pytest.raises(ValidationError, match="Available types"):
        S.add_module(make_spec(), "x", "warp_drive", {}, settings)


def test_wheel_count_through_motor_torque(make_spec):
    spec = make_spec("attitude_feedback_rw")
    assert S.wheel_count(spec, "rw_torque.motor_torque") == 3
