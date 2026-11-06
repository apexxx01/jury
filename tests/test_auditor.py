import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from jury import auditor

START = datetime.fromisoformat("2026-11-04T00:01:00+05:30")
END = datetime.fromisoformat("2026-11-07T23:59:00+05:30")


def _git(cwd, *args, date=None):
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


@pytest.fixture
def switch_repo(tmp_path: Path) -> Path:
    r = tmp_path / "good"
    r.mkdir()
    (r / "README.md").write_text(
        "# Good project\n\n## How Switch is used\n\nRooms and agents.\n\n## Setup\n\n```bash\nuv sync\nuv run good up\n```\n\n"
        "## Demo\n\nhttps://youtu.be/abc123\n\n![diagram](docs/flow.png)\n\n## Notes\n\n" + ("word " * 400)
    )
    (r / "pyproject.toml").write_text("[project]\nname='good'\n")
    (r / "uv.lock").write_text("")
    (r / "LICENSE").write_text("Apache")
    (r / ".env.example").write_text("SWITCH_URL=\n")
    (r / "templates").mkdir()
    (r / "templates" / "team.template.yaml").write_text(
        "agents:\n  - name: a\n  - name: b\nroom:\n  name: r\n  roles:\n    - name: x\n  docs:\n    - name: d\nkickoff: hi\nlinks: []\n"
    )
    (r / "good").mkdir()
    (r / "good" / "client.py").write_text('URL = f"/agents/{aid}/events"\nawait op("send_targeted_message")\nawait op("link_rooms")\n')
    (r / "tests").mkdir()
    for i in range(3):
        (r / "tests" / f"test_{i}.py").write_text("def test(): pass\n")
    (r / ".github" / "workflows").mkdir(parents=True)
    (r / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    _git(r, "init", "-q")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "old", date="2026-10-01T10:00:00+05:30")
    (r / "x.txt").write_text("1")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "during", date="2026-11-05T10:00:00+05:30")
    return r


def test_good_repo_scores_high(switch_repo):
    rep = auditor.analyze_tree(switch_repo, "o", "good")
    auditor.score(rep)
    assert rep.switch["templates"] == ["templates/team.template.yaml"]
    assert "good/client.py" in rep.switch["protocol_files"]
    assert {"room links", "roles", "room docs", "kickoff", "multi-agent team", "event stream"} <= set(rep.switch["features"])
    assert rep.scores["use_of_switch"] >= 9
    assert rep.scores["technical_execution"] >= 8
    assert rep.scores["presentation"] >= 9
    assert rep.readme["video"] and rep.readme["switch_section"]
    assert not rep.secrets
    assert not any("video" in f for f in rep.red_flags)


async def test_commit_window(switch_repo):
    c = await auditor.commit_window(switch_repo, START, END)
    assert (c["before"], c["during"], c["after"]) == (1, 1, 0)


def test_secrets_are_found_and_placeholders_ignored(tmp_path):
    r = tmp_path / "leaky"
    r.mkdir()
    (r / "README.md").write_text("# x\n")
    (r / "bot.py").write_text(
        'TOKEN = "ghp_' + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8" + '"\nOTHER = "sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"\n'
    )
    (r / ".env").write_text("DISCORD_TOKEN=reallysecretvalue123\n")
    rep = auditor.analyze_tree(r, "o", "leaky")
    auditor.score(rep)
    kinds = sorted(s["kind"] for s in rep.secrets)
    assert kinds == ["GitHub token", "committed .env"]
    assert all("ghp_a1B2" not in s["masked"] for s in rep.secrets)
    assert any("Rotate it" in f for f in rep.red_flags)
    assert any("No trace of Switch" in f for f in rep.red_flags)
    assert rep.scores["use_of_switch"] == 0


def test_render_short_and_detailed(switch_repo):
    rep = auditor.analyze_tree(switch_repo, "o", "good")
    auditor.score(rep)
    short = auditor.render(rep)
    assert "Auditor report" in short and "Secrets scan" in short
    assert len(auditor.render(rep, detailed=True)) > len(short)
