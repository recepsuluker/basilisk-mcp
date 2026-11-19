# Example prompts

Prompts that work well with basilisk-mcp in Claude Desktop or Claude Code. Claude
chooses the tools; you can ask it to show the tool calls or the generated script.

## Getting started

* "What can the Basilisk server do? List the module types and templates."
* "Describe the reaction_wheels module: parameters, units and ports."

## Orbit propagation

* "Propagate the Basilisk basic orbit example for one orbit and tell me the period
  and the energy drift."
* "Compare a 700 km sun-synchronous orbit with and without J2 over three days. Plot
  the RAAN and check the drift against the analytic J2 rate."
* "Put a spacecraft in a Mars orbit with a = 4000 km, e = 0.05 and i = 60 deg, with J2.
  What are the RAAN and argument-of-periapsis rates?"
* "Run the same LEO orbit with rk4 at 60 s, rk4 at 10 s and rkf45 at 60 s and compare
  the final position differences."

## Attitude control

* "Run the Basilisk reaction-wheel attitude feedback example and report the settling
  time and the peak wheel speeds."
* "Tune K and P so the error settles below 1 deg in under 6 minutes while the wheels
  stay below 4000 rpm. Show me a comparison plot of the candidates."
* "Turn on integral feedback (Ki = 0.0002) and compare the final error with the PD
  controller."
* "Replace the reaction wheels with an ideal torquer (ext_force_torque driven by the
  controller) and compare."

## Building from scratch

* "Create a session from scratch: a 300 kg spacecraft with inertia [40, 35, 25] kg m^2
  in a 550 km, 53 deg circular orbit, three HR12 wheels (25 Nms), nadir pointing,
  starting from a 0.05 rad/s tumble. Validate it before running."
* "Use four reaction wheels in a pyramid configuration (axes at 54.74 deg from +z) and
  check that the controller still converges."

## 3-D visualization (Vizard)

* "Run the reaction-wheel attitude example with Vizard and open it."
* "Show the nadir-pointing scenario in Vizard."
* "Open run r002 in Vizard."

## Reproducibility

* "Export the script for the last run." Then run it yourself:
  `python scenario.py --output results/`
* "Export the wheel speed recorder to CSV."
