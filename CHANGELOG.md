# Changelog

## 0.2.0 — 2026-11-19

* Vizard 3-D visualization: `run_simulation(..., vizard=true)` records `vizard.bin` (spacecraft, planet,
  body axes, reaction wheels); `open_in_vizard` tool, `basilisk-mcp vizard` command for a one-click desktop
  shortcut, Vizard auto-detection, optional `vizard_auto_open`, `max_vizard_frames`.
* `export_script(..., vizard=true)`.
* `doctor` reports whether Vizard was found.

## 0.1.0 — 2026-11-19

First public release.

* MCP server for Basilisk with 26 tools, 2 prompts and 2 resources, over stdio or local HTTP.
* Module catalog: spacecraft, gravity (point mass, J2 for Earth and Mars), external force/torque, simple
  navigation, reaction wheels (Honeywell HR12/14/16), inertial and Hill-frame pointing, attitude tracking
  error, MRP feedback, RW motor torque mapping.
* Templates mirroring Basilisk's `scenarioBasicOrbit`, `scenarioAttitudeFeedback` and
  `scenarioAttitudeFeedbackRW`, plus a nadir-pointing scenario.
* Code generation to standalone Basilisk scripts, run in isolated, resource-limited workers.
* Results: down-sampled queries, derived variables, orbit/attitude/actuator analyses, run comparison,
  PNG plots, CSV export.
* Tests reproducing the official Basilisk examples, J2 secular-rate checks and security tests.
