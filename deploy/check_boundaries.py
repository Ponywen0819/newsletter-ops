#!/usr/bin/env python3
"""依賴方向檢查：path 依賴以 editable 安裝，違規的 import 常常照樣跑得動，所以掃 AST 把關。

用法：python3 deploy/check_boundaries.py [--selftest]

  1. 成員只能 import 自己或 MEMBERS 允許的 newsletter_* 套件
  2. 成員 pyproject.toml 的 newsletter-* 依賴要與 MEMBERS 一致；沒有成員可以依賴 apps/ 底下的模組
  3. 不准用 `__file__`：資料目錄由執行環境決定（newsletter_shared.ROOT），不從原始碼位置推算
  4. 成員目錄都存在；repo 根沒有 src/、pyproject.toml、uv.lock（每個模組是獨立的 uv 專案）
  5. 每個 Nx 專案（*/*/project.json）都登記在 MEMBERS 或 NODE_MODULES，名稱是 newsletter-<id>，
     project.json、manifest（Python 再加 uv.lock）都有進版控
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 成員 id → (目錄, 允許依賴的成員 id)；套件名 newsletter_<id>，發行名 newsletter-<id>
MEMBERS = {
    "shared": (ROOT / "packages" / "shared", set()),
    "agent": (ROOT / "packages" / "agent", {"shared"}),
    "notify": (ROOT / "packages" / "notify", {"shared"}),
    "web": (ROOT / "web" / "server", {"shared", "agent"}),
    "pipeline": (ROOT / "apps" / "pipeline", {"shared", "agent", "notify"}),
}
# 非 Python 模組：id → 目錄；不能被 Python import
NODE_MODULES = {
    "web-ui": ROOT / "web" / "ui",
}


def _imported(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        names = [a.name for a in node.names]
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        names = [node.module]
    else:
        names = []
    return {n.split(".")[0] for n in names}


def _check_file(mid: str, base: Path, py: Path, ok: set[str]) -> list[str]:
    errors = []
    rel = f"{mid}/{py.relative_to(base)}"
    for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"), filename=rel)):
        for top in _imported(node):
            if top.startswith("newsletter_") and top not in ok:
                errors.append(f"{rel}:{node.lineno} {mid} 不可 import {top}")
        if isinstance(node, ast.Name) and node.id == "__file__":
            errors.append(f"{rel}:{node.lineno} 不准用 __file__（資料目錄用 newsletter_shared.ROOT）")
    return errors


def _check_deps(mid: str, base: Path, allowed: set[str]) -> list[str]:
    deps = tomllib.loads((base / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    declared = {m.group(0).lower().replace("_", "-") for d in deps if (m := re.match(r"[A-Za-z0-9_.-]+", d))}
    declared = {d for d in declared if d.startswith("newsletter-")}
    expected = {f"newsletter-{m}" for m in allowed}
    if declared != expected:
        return [f"成員 {mid} 的 pyproject.toml：newsletter-* 依賴是 {sorted(declared)}，應為 {sorted(expected)}"]
    return []


def check(members: dict = MEMBERS) -> list[str]:
    errors = []
    for mid, (base, allowed) in members.items():
        if not base.is_dir():
            errors.append(f"成員 {mid} 的目錄不存在：{base}")
            continue
        ok = {f"newsletter_{m}" for m in allowed | {mid}}
        errors += [f"成員 {mid} 不可依賴 apps/ 底下的 {a}" for a in sorted(allowed) if a in members and members[a][0].parent.name == "apps"]
        for py in sorted((base / "src").rglob("*.py")):
            errors += _check_file(mid, base, py, ok)
        errors += _check_deps(mid, base, allowed)
    return errors


def check_root(root: Path = ROOT) -> list[str]:
    return [f"repo 根不應有 {n}：每個模組是獨立的 uv 專案" for n in ("src", "pyproject.toml", "uv.lock") if (root / n).exists()]


def _tracked(root: Path) -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout
    return set(out.splitlines())


def check_projects(root: Path = ROOT, members: dict = MEMBERS, node: dict = NODE_MODULES) -> list[str]:
    errors = []
    registered = {base: mid for mid, (base, _) in members.items()} | {base: mid for mid, base in node.items()}
    tracked = _tracked(root)
    for pj in sorted(root.glob("*/*/project.json")):
        base = pj.parent
        rel = str(base.relative_to(root))
        if base not in registered:
            errors.append(f"{rel}/project.json 沒有登記在 MEMBERS 或 NODE_MODULES")
            continue
        want = f"newsletter-{registered[base]}"
        manifest = "package.json" if base in node.values() else "pyproject.toml"
        text = (base / manifest).read_text(encoding="utf-8")
        names = {"project.json": json.loads(pj.read_text(encoding="utf-8")).get("name"),
                 manifest: json.loads(text)["name"] if manifest == "package.json" else tomllib.loads(text)["project"]["name"]}
        errors += [f"{rel}/{f} 的名稱是 {n}，應為 {want}" for f, n in names.items() if n != want]
        files = ("project.json", manifest) if manifest == "package.json" else ("project.json", manifest, "uv.lock")
        errors += [f"{rel}/{f} 沒有進版控（.gitignore 白名單？）" for f in files if f"{rel}/{f}" not in tracked]
    for base, mid in registered.items():
        if not (base / "project.json").is_file():
            errors.append(f"模組 {mid} 沒有 project.json：{base}")
    return errors


def selftest() -> None:
    with tempfile.TemporaryDirectory() as d:
        base = Path(d) / "agent"
        (base / "src" / "newsletter_agent").mkdir(parents=True)
        (base / "pyproject.toml").write_text('[project]\nname = "newsletter-agent"\ndependencies = ["newsletter-shared", "claude-agent-sdk>=1"]\n')
        mod = base / "src" / "newsletter_agent" / "x.py"
        members = {"agent": (base, {"shared"})}

        mod.write_text("from newsletter_shared import metrics\nimport newsletter_agent.y\n")
        assert check(members) == [], check(members)

        mod.write_text("import newsletter_notify.render_email\n")
        assert any("不可 import newsletter_notify" in e for e in check(members))

        mod.write_text("from pathlib import Path\nHERE = Path(__file__)\n")
        assert any("__file__" in e for e in check(members))

        mod.write_text("")
        (base / "pyproject.toml").write_text('[project]\nname = "newsletter-agent"\ndependencies = ["newsletter-notify"]\n')
        assert any("pyproject.toml" in e for e in check(members))
        assert any("目錄不存在" in e for e in check({"notify": (Path(d) / "notify", {"shared"})}))

        root = Path(d) / "repo"
        root.mkdir()
        assert check_root(root) == [], check_root(root)
        (root / "uv.lock").write_text("")
        assert any("uv.lock" in e for e in check_root(root))
        (root / "uv.lock").unlink()

        apps = {"pipeline": (Path(d) / "apps" / "pipeline", set()), "agent": (base, {"pipeline"})}
        assert any("apps/" in e for e in check(apps))

        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        ui = root / "web" / "ui"
        ui.mkdir(parents=True)
        (ui / "package.json").write_text('{"name": "newsletter-web-ui"}')
        (ui / "project.json").write_text('{"name": "newsletter-web-ui"}')
        node = {"web-ui": ui}
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        assert check_projects(root, {}, node) == [], check_projects(root, {}, node)
        (ui / "package.json").write_text('{"name": "newsletter-web"}')
        assert any("應為 newsletter-web-ui" in e for e in check_projects(root, {}, node))
        assert any("沒有登記" in e for e in check_projects(root, {}, {}))
        subprocess.run(["git", "rm", "-q", "--cached", "web/ui/project.json"], cwd=root, check=True)
        assert any("沒有進版控" in e for e in check_projects(root, {}, node))

        py = root / "packages" / "shared"
        py.mkdir(parents=True)
        (py / "pyproject.toml").write_text('[project]\nname = "newsletter-shared"\n')
        (py / "project.json").write_text('{"name": "newsletter-shared"}')
        subprocess.run(["git", "add", "packages"], cwd=root, check=True)
        assert any("uv.lock 沒有進版控" in e for e in check_projects(root, {"shared": (py, set())}, {}))
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
        raise SystemExit(0)
    errs = check() + check_root() + check_projects()
    print("\n".join(errs) if errs else "ok", file=sys.stderr if errs else sys.stdout)
    raise SystemExit(1 if errs else 0)
