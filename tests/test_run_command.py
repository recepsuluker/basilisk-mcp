"""`basilisk-mcp run`: one-click scenario runs."""

import json
import sys

import pytest

from basilisk_mcp.cli import main
from basilisk_mcp.manager import Manager

from .conftest import requires_basilisk
from .test_vizard import fake_vizard, wait_for


def run_cli(args, capsys):
    with pytest.raises(SystemExit) as exc:
        main(args)
    return exc.value.code, capsys.readouterr()


def test_unknown_scenario(settings, capsys):
    code, out = run_cli(["--data-dir", str(settings.data_dir), "run", "warp_drive"], capsys)
    assert code == 2 and "Unknown scenario" in out.err


def test_session_without_runs_needs_duration(settings, capsys):
    session = Manager(settings).create_session(template="basic_orbit")
    code, out = run_cli(["--data-dir", str(settings.data_dir), "run", session.id], capsys)
    assert code == 2 and "--duration" in out.err


@requires_basilisk
@pytest.mark.basilisk
def test_template_run_reuses_session_and_summarizes(settings, capsys):
    base = ["--data-dir", str(settings.data_dir), "run"]
    code, out = run_cli([*base, "attitude_feedback_rw", "--duration", "120", "--no-vizard"], capsys)
    assert code == 0 and "attitude error" in out.out and "peak speed" in out.out
    code, out = run_cli([*base, "attitude_feedback_rw", "--duration", "120", "--no-vizard"], capsys)
    assert code == 0 and "Run r002 completed" in out.out
    sessions = Manager(settings).sessions.values()
    assert [s.name for s in sessions] == ["one-click attitude_feedback_rw"]


@requires_basilisk
@pytest.mark.basilisk
def test_short_run_does_not_report_mean_raan_rate(settings, capsys):
    code, out = run_cli(["--data-dir", str(settings.data_dir), "run", "basic_orbit", "--duration", "600",
                         "--no-vizard"], capsys)
    assert code == 0 and "period" in out.out and "RAAN rate" not in out.out


@requires_basilisk
@pytest.mark.basilisk
@pytest.mark.skipif(sys.platform == "win32", reason="fake executable is a POSIX script")
def test_saved_session_run_opens_vizard(settings, tmp_path, capsys, monkeypatch):
    exe, log = fake_vizard(tmp_path)
    monkeypatch.setenv("BASILISK_MCP_VIZARD_PATH", str(exe))
    session = Manager(settings).create_session(template="basic_orbit")
    code, out = run_cli(["--data-dir", str(settings.data_dir), "run", session.id, "--duration", "600"], capsys)
    assert code == 0 and "Opened Vizard" in out.out
    reloaded = Manager(settings)
    assert reloaded.get(session.id).runs == {}  # the source session itself is never touched
    copy = next(s for s in reloaded.sessions.values() if s.name == f"one-click {session.id}")
    assert copy.spec == reloaded.get(session.id).spec and len(copy.runs) == 1
    assert wait_for(log)
    assert json.loads(log.read_text())[0] == "-loadFile"


@requires_basilisk
@pytest.mark.basilisk
def test_run_ids_skip_folders_created_by_another_process(settings):
    import asyncio

    manager = Manager(settings)
    session = manager.create_session(template="basic_orbit")
    (manager.session_dir(session) / "runs" / "r001").mkdir(parents=True)  # e.g. written by the CLI
    record = asyncio.run(manager.run(session.id, 600.0))
    assert record["run_id"] == "r002"
