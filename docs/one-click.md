# One-click scenarios

`basilisk-mcp run` runs a scenario, prints a short physics summary, and opens the
result in [Vizard](vizard.md), all in one step. Put it behind a desktop shortcut and
a scenario is one double-click away.

```bash
basilisk-mcp run attitude_feedback_rw                  # a template, with Vizard
basilisk-mcp run basic_orbit --duration 86400          # one day of orbit propagation
basilisk-mcp run s1a2b3c4d                             # a session you built with Claude
```

```
Running 'one-click attitude_feedback_rw' (sf5923633) for 600 s with Vizard...
Run r001 completed in 2.3 s (Basilisk 2.12.2)
  attitude error  82.06 deg -> 0.1378 deg (below 1 deg after 418 s)
  wheels          peak speed 2917 rpm
Opened Vizard: C:\Users\you\.basilisk-mcp\sessions\sf5923633\runs\r001\vizard.bin
```

| Option | Meaning |
|---|---|
| `scenario` | a template (`basic_orbit`, `attitude_feedback_torque`, `attitude_feedback_rw`, `nadir_pointing_rw`) or a session id |
| `--duration S` | simulated seconds (default: the template's suggested duration, or the session's last run) |
| `--no-vizard` | do not record a Vizard file |
| `--no-open` | record the Vizard file but do not open Vizard |
| `--pause` | keep the console window open until Enter is pressed (useful for shortcuts) |

Template runs reuse one session per template (`one-click <template>`), so repeated
clicks add runs instead of filling the session list.

## From a chat to a one-click shortcut

1. Design the scenario with Claude: *"Build a 300 kg spacecraft in a 550 km orbit
   with three HR12 wheels, nadir pointing…"*.
2. Ask: *"What is the session id?"* (for example `s1a2b3c4d`).
3. Create a shortcut that runs `basilisk-mcp run s1a2b3c4d --pause`.

Each click runs a fresh copy of the session's **current** spec in a separate
`one-click s1a2b3c4d` session. If you keep refining the session in Claude, the
shortcut picks up the changes. The original session is never modified from outside,
so this is safe while Claude's server is running.

## Windows: create desktop shortcuts

Adjust `$exe` to where `basilisk-mcp.exe` is installed, then run in PowerShell:

```powershell
$exe = "$HOME\basilisk\.venv\Scripts\basilisk-mcp.exe"
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$scenarios = [ordered]@{
  "Basilisk - RW attitude control" = "attitude_feedback_rw"
  "Basilisk - Nadir pointing"      = "nadir_pointing_rw"
  "Basilisk - LEO orbit (1 day)"   = "basic_orbit --duration 86400"
  "Basilisk - Open last in Vizard" = $null
}
foreach ($name in $scenarios.Keys) {
  $lnk = $shell.CreateShortcut("$desktop\$name.lnk")
  $lnk.TargetPath = $exe
  $lnk.Arguments = if ($scenarios[$name]) { "run $($scenarios[$name]) --pause" } else { "vizard" }
  $lnk.WorkingDirectory = $HOME
  $lnk.Save()
}
```

For a session built in a chat, add one more entry, for example
`"Basilisk - My mission" = "s1a2b3c4d"`.

## macOS / Linux

Use shell aliases or desktop launchers, for example:

```bash
alias bsk-attitude='basilisk-mcp run attitude_feedback_rw'
alias bsk-orbit='basilisk-mcp run basic_orbit --duration 86400'
```
