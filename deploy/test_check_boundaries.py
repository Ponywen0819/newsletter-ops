import subprocess
from pathlib import Path

import pytest

from check_boundaries import check, check_projects, check_root


@pytest.fixture
def agent(tmp_path):
    base = tmp_path / "agent"
    (base / "src" / "newsletter_agent").mkdir(parents=True)
    (base / "pyproject.toml").write_text('[project]\nname = "newsletter-agent"\ndependencies = ["newsletter-shared", "claude-agent-sdk>=1"]\n')
    return base, base / "src" / "newsletter_agent" / "x.py", {"agent": (base, {"shared"})}


def test_imports(agent):
    _, mod, members = agent
    mod.write_text("from newsletter_shared import metrics\nimport newsletter_agent.y\n")
    assert check(members) == []
    mod.write_text("import newsletter_notify.render_email\n")
    assert any("不可 import newsletter_notify" in e for e in check(members))


def test_no_file_dunder(agent):
    _, mod, members = agent
    mod.write_text("from pathlib import Path\nHERE = Path(__file__)\n")
    assert any("__file__" in e for e in check(members))


def test_deps(agent, tmp_path):
    base, mod, members = agent
    mod.write_text("")
    (base / "pyproject.toml").write_text('[project]\nname = "newsletter-agent"\ndependencies = ["newsletter-notify"]\n')
    assert any("pyproject.toml" in e for e in check(members))
    assert any("目錄不存在" in e for e in check({"notify": (tmp_path / "notify", {"shared"})}))
    apps = {"worker": (tmp_path / "apps" / "worker", set()), "agent": (base, {"worker"})}
    assert any("apps/" in e for e in check(apps))


def test_root(tmp_path):
    assert check_root(tmp_path) == []
    (tmp_path / "uv.lock").write_text("")
    assert any("uv.lock" in e for e in check_root(tmp_path))


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True)


def test_projects(tmp_path):
    root = tmp_path
    git(root, "init", "-q")
    ui = root / "apps" / "web-ui"
    ui.mkdir(parents=True)
    (ui / "package.json").write_text('{"name": "newsletter-web-ui"}')
    (ui / "project.json").write_text('{"name": "web-ui"}')
    node = {"web-ui": ui}
    git(root, "add", ".")
    assert check_projects(root, {}, node) == []
    (ui / "package.json").write_text('{"name": "newsletter-web"}')
    assert any("應為 newsletter-web-ui" in e for e in check_projects(root, {}, node))
    (ui / "package.json").write_text('{"name": "newsletter-web-ui"}')
    (ui / "project.json").write_text('{"name": "newsletter-web-ui"}')
    assert any("project.json 的名稱是 newsletter-web-ui，應為 web-ui" in e for e in check_projects(root, {}, node))
    (ui / "project.json").write_text('{"name": "web-ui"}')
    assert any("沒有登記" in e for e in check_projects(root, {}, {}))
    git(root, "rm", "-q", "--cached", "apps/web-ui/project.json")
    assert any("沒有進版控" in e for e in check_projects(root, {}, node))


def test_python_lock_tracked(tmp_path):
    root = tmp_path
    git(root, "init", "-q")
    py = root / "packages" / "shared"
    py.mkdir(parents=True)
    (py / "pyproject.toml").write_text('[project]\nname = "newsletter-shared"\n')
    (py / "project.json").write_text('{"name": "shared"}')
    git(root, "add", "packages")
    assert any("uv.lock 沒有進版控" in e for e in check_projects(root, {"shared": (py, set())}, {}))
