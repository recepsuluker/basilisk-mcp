import asyncio, json, tempfile
import numpy as np
from basilisk_mcp.config import load_settings
from basilisk_mcp.manager import Manager
from basilisk_mcp import spec as S

m = Manager(load_settings(data_dir=tempfile.mkdtemp()))
out = {}
s = m.create_session(template="attitude_feedback_rw")
for label, K, P in [("K=3.5, P=30", 3.5, 30), ("K=7, P=40", 7, 40), ("K=12, P=60", 12, 60)]:
    m.mutate(s.id, None, lambda sp, K=K, P=P: S.set_params(sp, "control", {"K": K, "P": P}))
    r = asyncio.run(m.run(s.id, 600.0, label))
    d = m.run_data(s.id, r["run_id"])[2]
    out[label] = {"t": d.time("att_error_log").tolist(),
                  "err": d.get("att_error_log", "attitude_error_deg")[:, 0].tolist(),
                  "rpm": d.get("rw_speed_log", "wheel_speeds_rpm").tolist()}
o = m.create_session(template="basic_orbit", overrides={
    "earth": {"j2": True},
    "sc": {"orbit": {"semi_major_axis_km": 7000.0, "eccentricity": 0.05, "inclination_deg": 30.0,
                     "raan_deg": 0.0, "arg_periapsis_deg": 0.0, "true_anomaly_deg": 0.0}}})
m.mutate(o.id, None, lambda sp: (S.remove_recorder(sp, "states"), S.add_recorder(sp, "states", "sc.state", 60.0, m.settings)))
r = asyncio.run(m.run(o.id, 10 * 86400.0))
d = m.run_data(o.id, r["run_id"])[2]
out["orbit"] = {"t": d.time("states").tolist(), "r": (d.get("states", "r_BN_N") / 1000).tolist(),
                "raan": d.get("states", "raan_deg")[:, 0].tolist()}
from basilisk_mcp.results import analyze_orbit
a = analyze_orbit(d, "states", m.run_spec(m.get(o.id), r["run_id"]))
out["orbit_analysis"] = {"raan_rate": a["elements"]["raan_deg"]["mean_rate_deg_per_day"],
                         "raan_analytic": a["j2_analytic_rates_deg_per_day"]["raan"]}
json.dump(out, open("data.json", "w"))
print({k: (v["err"][-1] if "err" in v else None) for k, v in out.items() if k != "orbit_analysis"}, out["orbit_analysis"])
