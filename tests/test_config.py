from jury import config


def test_reads_switch_console_local_server(tmp_path, monkeypatch):
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(config.platform, "system", lambda: "Darwin")
    for k in ("SWITCH_URL", "SWITCH_REGISTRATION_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / "Library" / "Application Support" / "switchdash" / "local-switch-server" / ".env"
    env.parent.mkdir(parents=True)
    env.write_text('# written by Console\nAPI_HOST_PORT=8123\nAGENT_REGISTRATION_TOKEN="tok-abc"\nSWITCH_BIND_ADDR=127.0.0.1\n')
    cfg = config.load(cwd=tmp_path)
    assert cfg.switch_url == "http://127.0.0.1:8123"
    assert cfg.registration_token == "tok-abc"
    assert "Switch Console" in cfg.source


def test_environment_wins_over_console(tmp_path, monkeypatch):
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SWITCH_URL", "https://switch.example.com/")
    monkeypatch.setenv("SWITCH_REGISTRATION_TOKEN", "from-env")
    monkeypatch.setenv("JURY_JUDGES", "@a, b ,")
    monkeypatch.setenv("JURY_WINDOW_START", "2026-01-01T00:00:00+00:00")
    cfg = config.load(cwd=tmp_path)
    assert cfg.switch_url == "https://switch.example.com"
    assert cfg.registration_token == "from-env"
    assert cfg.judges == ["a", "b"]
    assert cfg.window_start.year == 2026 and cfg.window_start.month == 1


def test_env_file_parsing(tmp_path):
    p = tmp_path / ".env"
    p.write_text("export A=1\nB='two words'\n# c=3\nbroken line\nD=\n")
    assert config.read_env_file(p) == {"A": "1", "B": "two words", "D": ""}
    assert config.read_env_file(tmp_path / "missing") == {}


def test_newer_console_ports_json_gives_url_without_token(tmp_path, monkeypatch):
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(config.platform, "system", lambda: "Darwin")
    for k in ("SWITCH_URL", "SWITCH_REGISTRATION_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    d = tmp_path / "Library" / "Application Support" / "switchdash" / "local-switch-server"
    d.mkdir(parents=True)
    (d / "ports.json").write_text('{"gateway": 3000, "api": 8042}')
    (tmp_path / ".env").write_text("SWITCH_REGISTRATION_TOKEN=from-console-settings\n")
    cfg = config.load(cwd=tmp_path)
    assert cfg.switch_url == "http://127.0.0.1:8042"
    assert cfg.registration_token == "from-console-settings"
