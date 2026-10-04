#!/usr/bin/env python3
"""依賴方向檢查：各成員只能 import SPEC.md 的 Capability Map 允許的 newsletter_* 套件。

用法：python3 deploy/check_boundaries.py [--selftest]

所有成員裝在同一個 venv，違規的 import 不會報錯，所以靠掃 AST 把關：
  1. 成員原始碼的 `import newsletter_x` 只能是自己，或下表允許的成員
  2. 成員 pyproject.toml 宣告的 newsletter-* 依賴要與下表完全一致
  3. `__file__` 只准出現在 shared 的 paths.py（repo 根只從那裡取）
成員目錄還不存在就略過（搬移期間逐步加入）。只用標準庫。
"""
from __future__ import annotations

import ast
import re
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 成員 id → (目錄, 允許依賴的成員 id)；套件名 newsletter_<id>，發行名 newsletter-<id>
MEMBERS = {
    "shared": (ROOT / "shared", set()),
    "agent": (ROOT / "agent", {"shared"}),
    "notify": (ROOT / "notify", {"shared"}),
    "web": (ROOT / "web" / "server", {"shared", "agent"}),
}


def check(members: dict = MEMBERS) -> list[str]:
    errors = []
    for mid, (base, allowed) in members.items():
        if not base.is_dir():
            continue
        ok = {f"newsletter_{m}" for m in allowed | {mid}}
        for py in sorted((base / "src").rglob("*.py")):
            rel = f"{mid}/{py.relative_to(base)}"
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"), filename=rel)):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                else:
                    names = []
                for top in {n.split(".")[0] for n in names}:
                    if top.startswith("newsletter_") and top not in ok:
                        errors.append(f"{rel}:{node.lineno} {mid} 不可 import {top}")
                if isinstance(node, ast.Name) and node.id == "__file__" and not (mid == "shared" and py.name == "paths.py"):
                    errors.append(f"{rel}:{node.lineno} __file__ 只准出現在 shared 的 paths.py")
        deps = tomllib.loads((base / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
        declared = {m.group(0).lower().replace("_", "-") for d in deps if (m := re.match(r"[A-Za-z0-9_.-]+", d))}
        declared = {d for d in declared if d.startswith("newsletter-")}
        expected = {f"newsletter-{m}" for m in allowed}
        if declared != expected:
            errors.append(f"{mid}/pyproject.toml 的 newsletter-* 依賴是 {sorted(declared)}，應為 {sorted(expected)}")
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
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
        raise SystemExit(0)
    errs = check()
    print("\n".join(errs) if errs else "ok", file=sys.stderr if errs else sys.stdout)
    raise SystemExit(1 if errs else 0)
