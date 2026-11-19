# Configuration

Settings come from four layers; later layers win:

1. built-in defaults,
2. a TOML file passed with `--config` (`basilisk-mcp init-config` writes a template),
3. environment variables `BASILISK_MCP_<SETTING>` (e.g. `BASILISK_MCP_RUN_TIMEOUT_S=600`),
4. command-line flags (`--data-dir`, `--python`, `serve --log-level`).

```toml
[server]
data_dir = "~/.basilisk-mcp"
python = "/path/to/python-with-basilisk"
run_timeout_s = 300.0
```

| Setting | Default | Meaning |
|---|---|---|
| `data_dir` | `~/.basilisk-mcp` | Sessions, generated scripts, results, plots and CSV exports. Created with `0700` permissions. |
| `python` | the server's interpreter | Interpreter for simulation workers; must be able to `import Basilisk`. |
| `max_sessions` | 16 | Open sessions. |
| `max_modules` | 32 | Modules per session. |
| `max_recorders` | 16 | Recorders per session. |
| `max_runs_per_session` | 200 | Stored runs per session. |
| `max_concurrent_runs` | 2 | Simulations running at the same time; extra runs wait. |
| `run_timeout_s` | 300 | Wall-clock limit per run; the worker's process group is killed when exceeded. |
| `max_sim_duration_s` | 31 557 600 | Longest simulated time per run (one year). |
| `max_steps` | 5 000 000 | Integration steps per task per run (`duration_s / dt_s`). |
| `max_recorded_samples` | 200 000 | Samples per recorder per run (`duration_s / sample_dt_s`). |
| `max_query_points` | 2 000 | Upper bound for `query_results(max_points=...)`. |
| `memory_limit_mb` | 4 096 | Worker address-space limit (Linux `RLIMIT_AS`); 0 disables. |
| `max_file_size_mb` | 512 | Largest file a worker may write (POSIX `RLIMIT_FSIZE`); 0 disables. |
| `max_log_bytes` | 16 384 | Worker stdout/stderr returned on failure. |
| `log_level` | `INFO` | Server log level (stderr). |
| `vizard_path` | auto-detect | Path to `Vizard.exe` / `Vizard.app` / Linux binary for `open_in_vizard` and `basilisk-mcp vizard`. |
| `vizard_auto_open` | `false` | Open Vizard automatically after `run_simulation(..., vizard=true)`. |
| `max_vizard_frames` | 20 000 | Frames per Vizard file; the frame period is stretched to stay below it. |

Environment-only:

| Variable | Meaning |
|---|---|
| `BASILISK_MCP_TOKEN` | Bearer token for `serve --transport http` (at least 32 characters; `basilisk-mcp token` generates one). |

## Data directory layout

```
~/.basilisk-mcp/
└── sessions/
    └── s1a2b3c4d/
        ├── session.json              spec, version, run records
        ├── scenario_v4.py            from export_script
        └── runs/
            └── r001/
                ├── scenario.py       the exact script that ran
                ├── spec.json         spec snapshot of this run
                ├── results.npz       recorded data
                ├── summary.json      Basilisk version, gravity constants, recorder metadata
                ├── vizard.bin        Vizard 3-D file (runs made with vizard=true)
                ├── stdout.log / stderr.log
                ├── plots/plot_*.png
                └── exports/<recorder>.csv
```

Sessions are reloaded when the server restarts. Stored specs are replayed through
the same validation as live edits, so a hand-edited or corrupted `session.json` is
skipped with a warning rather than trusted. `close_session` renames
`session.json` to `session.closed.json` (or deletes the folder with `delete_files=true`).
