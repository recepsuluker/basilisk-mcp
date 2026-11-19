"""Code generation and security properties."""

import ast
import json

import pytest

from basilisk_mcp import spec as S
from basilisk_mcp.codegen import generate_script
from basilisk_mcp.errors import ValidationError
from basilisk_mcp.manager import Manager
from basilisk_mcp.templates import TEMPLATES

ALLOWED_IMPORT_ROOTS = {"argparse", "json", "pathlib", "time", "numpy", "Basilisk"}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "system", "popen", "getattr", "setattr"}


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_generated_script_only_uses_expected_imports_and_calls(name, make_spec):
    source = generate_script(make_spec(name), 100.0)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert {a.name.split(".")[0] for a in node.names} <= ALLOWED_IMPORT_ROOTS
        elif isinstance(node, ast.ImportFrom):
            assert node.module.split(".")[0] in ALLOWED_IMPORT_ROOTS
        elif isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            assert called not in FORBIDDEN_CALLS, called


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_generation_is_deterministic(name, make_spec):
    assert generate_script(make_spec(name), 60.0) == generate_script(make_spec(name), 60.0)


def test_string_constants_come_from_fixed_vocabulary(make_spec):
    """Every string literal in the script is a module name, a fixed token, or generator boilerplate."""
    spec = make_spec("attitude_feedback_rw")
    tree = ast.parse(generate_script(spec, 60.0))
    names = set(spec["modules"]) | set(spec["recorders"])
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            tokens = set(text.replace("__", " ").replace(".", " ").split())
            assert len(text) < 200 or text.startswith("Basilisk scenario")
            assert not ({"import", "os", "subprocess", "eval"} & tokens)
            if "__" in text and not text.startswith("__"):
                assert text.split("__")[0] in names


@pytest.mark.parametrize("payload", [
    "x\"); import os; os.system(\"id\")#",
    "a\nimport os",
    "__import__('os')",
    "sc' or 1",
    "A",
])
def test_injection_names_rejected_everywhere(payload, make_spec, settings):
    spec = make_spec("basic_orbit")
    with pytest.raises(ValidationError):
        S.add_module(spec, payload, "simple_nav", {}, settings)
    with pytest.raises(ValidationError):
        S.add_recorder(spec, payload, "sc.state", 10, settings)
    with pytest.raises(ValidationError):
        S.connect(spec, f"{payload}.state", "sc.state")
    with pytest.raises(ValidationError):
        S.set_params(spec, "earth", {"attach_to": [payload]})


def test_tampered_spec_cannot_reach_codegen(make_spec):
    spec = make_spec("basic_orbit")
    spec["modules"]["bad name"] = spec["modules"].pop("earth")
    with pytest.raises(ValidationError):
        generate_script(spec, 10.0)


def test_tampered_session_file_is_skipped(settings):
    manager = Manager(settings)
    session = manager.create_session(template="basic_orbit")
    path = manager.sessions_dir / session.id / "session.json"
    raw = json.loads(path.read_text())
    raw["spec"]["modules"]["sc"]["params"]["mass_kg"] = "__import__('os')"
    path.write_text(json.dumps(raw))
    assert session.id not in Manager(settings).sessions


def test_session_and_run_ids_are_validated(settings):
    manager = Manager(settings)
    for bad in ["../etc", "s123", "S1234abcd", "s1234abcd/..", None, 5]:
        with pytest.raises(ValidationError):
            manager.get(bad)
    session = manager.create_session(template="basic_orbit")
    for bad in ["../../x", "r1", "run1"]:
        with pytest.raises(ValidationError):
            manager.run_record(session, bad)


def test_data_dir_is_private(settings):
    import os
    import stat

    Manager(settings)
    if os.name == "posix":
        assert stat.S_IMODE(settings.data_dir.stat().st_mode) == 0o700


def test_edit_is_atomic_and_versioned(settings):
    manager = Manager(settings)
    session = manager.create_session(template="basic_orbit")
    before = json.dumps(session.spec, sort_keys=True)
    with pytest.raises(ValidationError):
        manager.mutate(session.id, None, lambda sp: (S.set_params(sp, "sc", {"mass_kg": 10}),
                                                    S.set_params(sp, "sc", {"mass_kg": -1})))
    assert json.dumps(session.spec, sort_keys=True) == before and session.version == 1
    from basilisk_mcp.errors import Conflict

    with pytest.raises(Conflict):
        manager.mutate(session.id, 7, lambda sp: None)


def test_session_limit(settings):
    from basilisk_mcp.errors import LimitExceeded

    settings.max_sessions = 1
    manager = Manager(settings)
    manager.create_session()
    with pytest.raises(LimitExceeded):
        manager.create_session()


def test_bearer_auth_middleware():
    import asyncio

    from basilisk_mcp.cli import BearerAuth

    calls = []

    async def app(scope, receive, send):
        calls.append(scope["path"])

    sent = []

    async def send(message):
        sent.append(message)

    token = "t" * 40
    mw = BearerAuth(app, token)
    asyncio.run(mw({"type": "http", "path": "/mcp", "headers": [(b"authorization", b"Bearer wrong")]}, None, send))
    assert sent[0]["status"] == 401 and not calls
    asyncio.run(mw({"type": "http", "path": "/mcp", "headers": [(b"authorization", f"Bearer {token}".encode())]},
                   None, send))
    assert calls == ["/mcp"]


def test_http_refuses_non_loopback_and_short_token(monkeypatch, capsys, tmp_path):
    from basilisk_mcp.cli import build_parser, cmd_serve

    args = build_parser().parse_args(["--data-dir", str(tmp_path), "serve", "--transport", "http", "--host", "0.0.0.0"])
    assert cmd_serve(args) == 2
    assert "non-loopback" in capsys.readouterr().err
    monkeypatch.setenv("BASILISK_MCP_TOKEN", "short")
    args = build_parser().parse_args(["--data-dir", str(tmp_path), "serve", "--transport", "http"])
    assert cmd_serve(args) == 2
    assert "at least 32" in capsys.readouterr().err
