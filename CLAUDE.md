# 分支（git flow）

- `main`：發行版本，正式機只跟 `main`。不在 `main` 上開發，也不直接開 PR 到 `main`。
- `develop`：整合分支（GitHub 的預設分支），所有修改先合到這裡。
- `feature/<name>`：從 `develop` 開，PR 合回 `develop`。
- 發行：開 `develop` → `main` 的 PR，合併後在 `main` 打 tag。正式機急修走 `hotfix/<name>`（從 `main` 開，合回 `main` 與 `develop`）。

專案說明見 [README.md](README.md)。
