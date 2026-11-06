from pathlib import Path

from jury.similarity import FingerprintIndex, fingerprint_tree

CORE = "\n".join(f"def handler_{i}(event, room):  total = compute_score(event.payload, weight={i})" for i in range(60))
BOILER = "\n".join(f"    configure_logging(level='INFO', name='app_{i}')" for i in range(40))


def repo(tmp: Path, name: str, *chunks: str) -> Path:
    r = tmp / name
    r.mkdir()
    (r / "main.py").write_text("import os\nfrom x import y\n" + "\n".join(chunks))
    (r / "README.md").write_text("ignored, not code")
    return r


def test_fingerprint_skips_short_and_import_lines(tmp_path):
    r = repo(tmp_path, "a", "x = 1\nreturn y\n}\n" + CORE)
    fp = fingerprint_tree(r)
    assert len(fp) == 60  # only the 60 long distinctive lines


def test_copy_flagged_and_boilerplate_ignored(tmp_path):
    idx = FingerprintIndex(tmp_path / "fp.json")
    # four honest teams share framework boilerplate; one copies team a's core
    for name, extra in (
        ("a", CORE),
        ("b", "unique_b_line_with_enough_length_here = 1"),
        ("c", "unique_c_line_with_enough_length_here = 2"),
        ("d", "unique_d_line_with_enough_length_here = 3"),
    ):
        idx.put(f"team/{name}", fingerprint_tree(repo(tmp_path, name, BOILER, extra)), [f"sha-{name}"])
    idx.put("team/copycat", fingerprint_tree(repo(tmp_path, "copycat", BOILER, CORE)), ["sha-copycat"])
    matches = idx.compare("team/copycat")
    assert [m.other for m in matches] == ["team/a"]
    assert matches[0].shared_code > 0.9
    # the honest teams share only boilerplate, which is discounted
    assert idx.compare("team/b") == []
    assert "Possible copy" in matches[0].describe("Case #001")


def test_shared_git_history_flagged(tmp_path):
    idx = FingerprintIndex(tmp_path / "fp.json")
    idx.put("x/one", ["h1"], ["base", "c1"])
    idx.put("x/fork", ["h2"], ["base", "c2"])
    m = idx.compare("x/fork")
    assert m and m[0].other == "x/one" and m[0].shared_commits == 1
    assert "git history" in m[0].describe("Case #1")
    # persisted
    assert FingerprintIndex(tmp_path / "fp.json").compare("x/one")[0].other == "x/fork"
