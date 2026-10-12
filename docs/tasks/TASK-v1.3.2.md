# TASK v1.3.2 — 公開前安全處理

目標：讓 repo 可安全公開。兩部分：內容脫敏＋git 歷史作者重寫。**最後不要自己 push，回報後由 Hermes 驗收**。

## A. 內容脫敏（真實內網 IP → 佔位符）

所有 git-tracked 檔案中的真實 IP 取代為佔位符：
- `dgx-host` → `dgx-host`（TensorFold 與 glances 所在主機）
- `dgx-host-2` → `dgx-host-2`（第二節點）
- 其他 192.168.x.x 一律比照改為語義化佔位符
- docs/tasks/ 歷史規格書也要清（公開後任何人可讀歷史檔案，不只最新版）

**重要：本機服務不能斷**
- `docker-compose.override.yml`（新增，加入 .gitignore）放置真實值：`METRICS_URL=http://dgx-host:8000/metrics`、`GPU_STATS_URLS=spark-23c2=http://dgx-host:61208/api/4/gpu,spark-6a75=http://dgx-host-2:61208/api/4/gpu`
- tracked 的 `docker-compose.yml` 與 `app.py` 預設值改為佔位符（如 `http://dgx-host:8000/metrics`）
- 改完後 `docker compose up -d --build`（compose 自動疊加 override），驗證 `curl localhost:8901/json` 一切正常

## B. README 更新

- 新增「設定」一節：說明使用者需自行設定 `METRICS_URL`（TensorFold metrics endpoint）與 `GPU_STATS_URLS`（glances API，`名稱=URL` 逗號分隔），建議用 `docker-compose.override.yml`（已 gitignore）放自己的環境值，並給完整範例
- 佔位符對照說明：`dgx-host`＝跑 TensorFold 的主機、`dgx-host-2`＝第二台 GPU 節點

## C. git 歷史作者重寫

- 所有 commit 的 author 與 committer 改為 `rykersun <rykersun@users.noreply.github.com>`（目前是 "Jessica"）
- 用 `git filter-branch --env-filter` 或 `git filter-repo`（擇一，勿安裝新套件就用 filter-branch）
- 重寫後 commit 內容雜湊會變，屬預期

## D. 完成後驗證（勿 push）

1. `git grep -E '192\.168\.'` → 無結果
2. `git log --format='%an <%ae>%n%cn <%ce>' | sort -u` → 只有 rykersun
3. `git log -p --all | grep -c '192\.168\.'` → 0（歷史也乾淨）
4. `curl -s localhost:8901/json` 正常
5. `git status` 乾淨（override 檔被 ignore）
6. 把以上輸出貼在報告
