# TASK v1.3.1 — 文件重整

對現有 dgx-in-use repo 做文件重整，**不改任何程式碼**。

## 工作項目

1. `git mv TASK.md TASK-v1.1.md TASK-v1.2.md TASK-v1.3.md docs/tasks/`（保留 git 歷史）
2. 新增根目錄 `CLAUDE.md`——給 Claude Code 的常駐專案上下文，內容需涵蓋：
   - 專案用途：顯示 dgx 叢集 TensorFold 模型 API 使用狀態的小型 Docker 服務
   - 架構：`app.py`（純 stdlib http.server，每次請求即時抓 metrics）、Docker（python:3-alpine＋tzdata）、docker-compose
   - 環境變數表：`PORT`、`METRICS_URL`、`GPU_STATS_URLS`（`名稱=URL` 逗號分隔，向下相容單一 `GPU_STATS_URL`）、`TZ`、`LAST_ACTIVE_FILE`
   - 資料來源：TensorFold `/metrics`（requests_running/waiting）；glances `/api/4/gpu`（第一個元素的 `proc` 欄位＝GPU 使用率，null/缺欄位/連不上 → N/A）
   - 慣例：註解與 UI 用繁體中文（台灣用語）；last_active 以原子寫入（.tmp + os.replace）持久化到 /data/last_active.json，寫檔失敗不可掛服務；時間一律 Asia/Taipei
   - 驗證慣例：改動後 `docker compose up -d --build`，然後 curl `/json`（欄位正確）與 `/`（中文狀態字樣），把輸出貼在報告
   - 禁止事項：不破壞自動刷新（meta refresh）、last_active 持久化、多 GPU 容錯；小改動不重構
3. `README.md` 微調：
   - 加「版本歷史見 docs/tasks/」一節
   - 加「CLAUDE.md 說明專案慣例，修改前先讀」一句
   - 其餘內容不動
4. git commit（訊息用繁體中文，格式：`docs: 文件重整——TASK 檔移至 docs/tasks/、新增 CLAUDE.md`）

## 驗證

1. `git status` 乾淨、`git log --oneline -2` 顯示新 commit
2. `git show --stat HEAD` 確認只有檔案搬移與三個文件變更（無 app.py/Dockerfile/compose 變動）
3. `ls docs/tasks/` 含 4 個 TASK 檔
4. 把輸出貼在報告。
