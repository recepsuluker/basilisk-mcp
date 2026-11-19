#!/usr/bin/env python3
"""Generate reference values from the *official* Basilisk example scenarios.

The official scripts are executed unmodified from a Basilisk source checkout. A
profiler hook captures the local variables of their ``run()`` function on return,
so the recorded data can be read without editing the examples. The resulting
numbers are stored in ``tests/data/reference.json`` and the test suite checks that
the same scenarios built through basilisk-mcp reproduce them.

Usage:
    git clone --depth 1 https://github.com/AVSLab/basilisk.git /tmp/basilisk
    python tools/generate_reference.py /tmp/basilisk
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import numpy as np  # noqa: E402
from Basilisk import __version__ as BSK_VERSION  # noqa: E402


def capture_run_locals(path: Path, **kwargs) -> dict:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    captured: dict = {}

    def profiler(frame, event, arg):
        if event == "return" and frame.f_code.co_name == "run" and frame.f_code.co_filename == str(path):
            captured.update(frame.f_locals)

    sys.setprofile(profiler)
    try:
        module.run(**kwargs)
    finally:
        sys.setprofile(None)
        import matplotlib.pyplot as plt

        plt.close("all")
    return captured


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("basilisk_repo", type=Path)
    parser.add_argument("--out", type=Path, default=Path(__file__).parents[1] / "tests" / "data" / "reference.json")
    args = parser.parse_args()
    examples = args.basilisk_repo / "examples"
    out: dict = {"basilisk_version": BSK_VERSION}

    # scenarioAttitudeFeedbackRW: default case (no jitter, no voltage interface).
    loc = capture_run_locals(examples / "scenarioAttitudeFeedbackRW.py", show_plots=False,
                             useJitterSimple=False, useRWVoltageIO=False)
    t = np.asarray(loc["attErrorLog"].times()) * 1e-9
    sigma = np.asarray(loc["attErrorLog"].sigma_BR)
    omega = np.asarray(loc["rwSpeedLog"].wheelSpeeds)[:, :3] if "rwSpeedLog" in loc \
        else np.asarray(loc["mrpLog"].wheelSpeeds)[:, :3]
    motor = np.asarray(loc["rwMotorLog"].motorTorque)[:, :3]
    out["attitude_feedback_rw"] = {
        "source": "examples/scenarioAttitudeFeedbackRW.py run(False, False, False)",
        "duration_s": float(t[-1]),
        "sample_times_s": t.tolist(),
        "sigma_BR": sigma.tolist(),
        "wheel_speeds_radps": omega.tolist(),
        "motor_torque_Nm": motor.tolist(),
    }

    # scenarioBasicOrbit: LEO, point-mass Earth.
    loc = capture_run_locals(examples / "scenarioBasicOrbit.py", show_plots=False, orbitCase="LEO",
                             useSphericalHarmonics=False, planetCase="Earth")
    rec = loc["dataRec"]
    out["basic_orbit"] = {
        "source": "examples/scenarioBasicOrbit.py run(False, 'LEO', False, 'Earth')",
        "duration_s": float(loc["simulationTime"]) * 1e-9,
        "sample_times_s": (np.asarray(rec.times()) * 1e-9).tolist(),
        "r_BN_N": np.asarray(rec.r_BN_N).tolist(),
        "v_BN_N": np.asarray(rec.v_BN_N).tolist(),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out))
    print(f"Wrote {args.out} (Basilisk {BSK_VERSION})")


if __name__ == "__main__":
    main()
