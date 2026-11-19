import math

import pytest

from basilisk_mcp.errors import ValidationError
from basilisk_mcp.registry import MODULE_TYPES, check_name, get_module_type


def sc(**params):
    return get_module_type("spacecraft").normalize_params({"orbit": {"altitude_km": 500}, **params})


def test_defaults_are_filled():
    p = sc()
    assert p["mass_kg"] == 750.0
    assert p["inertia_kgm2"][0][0] == 900.0
    assert p["orbit"]["eccentricity"] == 0.0
    assert p["sigma_BN"] == [0.0, 0.0, 0.0]


@pytest.mark.parametrize("inertia", [[1, 2, 2.5], [1, 0, 0, 0, 2, 0, 0, 0, 2.5], [[1, 0, 0], [0, 2, 0], [0, 0, 2.5]]])
def test_inertia_forms(inertia):
    assert sc(inertia_kgm2=inertia)["inertia_kgm2"] == [[1.0, 0, 0], [0, 2.0, 0], [0, 0, 2.5]]


@pytest.mark.parametrize("inertia", [[1, 1, 5], [-1, 2, 2], [[1, 0.5, 0], [0, 1, 0], [0, 0, 1]], [1, 2]])
def test_bad_inertia_rejected(inertia):
    with pytest.raises(ValidationError):
        sc(inertia_kgm2=inertia)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 1e31, "750", True, None])
def test_bad_numbers_rejected(value):
    with pytest.raises(ValidationError):
        sc(mass_kg=value)


def test_bounds_and_unknown_keys():
    with pytest.raises(ValidationError, match="must be >"):
        sc(mass_kg=0)
    with pytest.raises(ValidationError, match="Unknown parameter"):
        sc(colour="red")


def test_orbit_needs_exactly_one_size():
    with pytest.raises(ValidationError, match="exactly one"):
        get_module_type("spacecraft").normalize_params({"orbit": {"inclination_deg": 10}})
    with pytest.raises(ValidationError, match="exactly one"):
        get_module_type("spacecraft").normalize_params({"orbit": {"altitude_km": 1, "semi_major_axis_km": 7000}})
    with pytest.raises(ValidationError, match="Unknown orbit"):
        get_module_type("spacecraft").normalize_params({"orbit": {"altitude_km": 1, "period": 3}})


def test_orbit_and_cartesian_are_exclusive():
    with pytest.raises(ValidationError, match="not both"):
        sc(position_m=[7e6, 0, 0], velocity_mps=[0, 7.5e3, 0])
    with pytest.raises(ValidationError, match="together"):
        get_module_type("spacecraft").normalize_params({"position_m": [7e6, 0, 0]})


def test_enum_and_j2_restriction():
    g = get_module_type("gravity")
    assert g.normalize_params({"attach_to": ["sc"], "central_body": "mars", "j2": True})["j2"] is True
    with pytest.raises(ValidationError, match="J2"):
        g.normalize_params({"attach_to": ["sc"], "central_body": "moon", "j2": True})
    with pytest.raises(ValidationError, match="one of"):
        g.normalize_params({"attach_to": ["sc"], "central_body": "pluto"})


def test_reaction_wheel_rules():
    rw = get_module_type("reaction_wheels")
    with pytest.raises(ValidationError, match="supports max_momentum"):
        rw.normalize_params({"attach_to": "sc", "max_momentum_Nms": 60})
    with pytest.raises(ValidationError, match="initial_speeds_rpm"):
        rw.normalize_params({"attach_to": "sc", "initial_speeds_rpm": [1, 2]})
    with pytest.raises(ValidationError, match="zero vector"):
        rw.normalize_params({"attach_to": "sc", "spin_axes_B": [[0, 0, 0]]})
    p = rw.normalize_params({"attach_to": "sc", "model": "Honeywell_HR12", "max_momentum_Nms": 12})
    assert len(p["spin_axes_B"]) == 3


def test_partial_update_keeps_base():
    mt = get_module_type("mrp_feedback")
    base = mt.normalize_params({"inertia_from": "sc", "K": 5})
    updated = mt.normalize_params({"P": 10}, base=base)
    assert updated["K"] == 5 and updated["P"] == 10


@pytest.mark.parametrize("name", ["Sc", "1sc", "sc-1", "a" * 33, "", "sim", "sc; import os", "../x", None, 3])
def test_bad_names(name):
    with pytest.raises(ValidationError):
        check_name(name)


def test_every_module_describes_itself():
    for mt in MODULE_TYPES.values():
        d = mt.describe()
        assert d["type"] == mt.name and d["category"] in ("dynamics", "fsw")
        for p in d["params"].values():
            assert p["unit"]


def test_mrp_angle_definition():
    import numpy as np

    from basilisk_mcp.results import mrp_angle_deg

    assert math.isclose(mrp_angle_deg(np.array([[math.tan(math.radians(90) / 4), 0, 0]]))[0], 90.0)
