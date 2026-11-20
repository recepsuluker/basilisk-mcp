import json

from basilisk_mcp.cli import main


def run(args, capsys):
    try:
        main(args)
    except SystemExit as exc:
        assert exc.code == 0
    return capsys.readouterr().out


def test_print_config(capsys, tmp_path):
    out = run(["print-config", "claude-desktop"], capsys)
    assert json.loads(out)["mcpServers"]["basilisk"]["args"][-1] == "serve"
    assert run(["print-config", "claude-code"], capsys).startswith("claude mcp add basilisk -- ")


def test_token_and_init_config(capsys, tmp_path):
    assert len(run(["token"], capsys).strip()) >= 40
    path = tmp_path / "cfg.toml"
    run(["init-config", str(path)], capsys)
    from basilisk_mcp.config import load_settings

    assert load_settings(path, data_dir=tmp_path).max_sessions == 16


def test_install_claude_merges_and_backs_up(capsys, tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    original = {"mcpServers": {"notebooklm-mcp": {"command": "notebooklm-mcp"}}, "preferences": {"sidebarMode": "chat"}}
    cfg.write_bytes(b"\xef\xbb\xbf" + json.dumps(original).encode())  # tolerate a BOM written by other tools
    out = run(["install-claude", "--config-file", str(cfg), "--vizard-auto-open"], capsys)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert not cfg.read_bytes().startswith(b"\xef\xbb\xbf")
    assert data["mcpServers"]["notebooklm-mcp"] == {"command": "notebooklm-mcp"}
    assert data["preferences"] == {"sidebarMode": "chat"}
    entry = data["mcpServers"]["basilisk"]
    assert entry["args"] == ["serve"] and entry["env"]["BASILISK_MCP_VIZARD_AUTO_OPEN"] == "true"
    assert (tmp_path / "claude_desktop_config.json.bak").is_file()
    assert "Kept your other servers: notebooklm-mcp" in out
    out = run(["install-claude", "--config-file", str(cfg)], capsys)
    assert "Updated" in out and "env" not in json.loads(cfg.read_text())["mcpServers"]["basilisk"]


def test_install_claude_creates_file_and_dry_run(capsys, tmp_path):
    cfg = tmp_path / "Claude" / "claude_desktop_config.json"
    out = run(["install-claude", "--config-file", str(cfg), "--dry-run"], capsys)
    assert "dry run" in out and not cfg.exists()
    run(["install-claude", "--config-file", str(cfg)], capsys)
    assert json.loads(cfg.read_text())["mcpServers"]["basilisk"]["args"] == ["serve"]
