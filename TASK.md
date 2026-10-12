# 任務：dgx-in-use v1

在這個目錄建立一個小型 Docker 服務，用於顯示 DGX 叢集上 TensorFold 模型 API 目前的使用狀態。使用者是兩個人，他們在手機瀏覽器打開頁面就能知道「現在有没有人在用模型」，不必再互相傳訊息確認。

## 背景

- TensorFold 模型 API 跑在遠端機器 dgx，metrics endpoint 為 `http://dgx-host:8000/metrics`（Prometheus 文字格式）。
- 關鍵指標（已確認存在）：
  - `tensorfold:requests_running <N>`
  - `tensorfold:requests_waiting <N>`
  - `tensorfold:kv_cache_usage_ratio{pool="0"} <f>`
  - `tensorfold:prompt_tokens_total`、`tensorfold:generation_tokens_total`（累計 counter）
- 從這台機器 curl 上述 URL 已確認 200。

## 規格

1. **app.py**（只用 Python 標準庫，零第三方依賴）：
   - 用 `http.server` 在 `$PORT`（預設 8901）提供服務。
   - `GET /`：回傳 zh-TW HTML 頁面，`<meta http-equiv="refresh" content="5">` 自動刷新，不用任何 JS 框架。
   - `GET /json`：回傳 JSON 狀態（方便之後程式串接）。
   - 每次請求時即時抓 METRICS_URL（timeout 3 秒），解析上述指標。
   - 顯示內容（大字、一眼判讀）：
     - `● 使用中：N 個請求執行中`（若 waiting > 0 加「、M 個排隊中」），紅/橘色
     - 或 `○ 閒置中（沒有請求）`，綠色
     - API 連不上時：`⚠ API 無回應`，灰色
     - 次要資訊：kv cache 使用率、最後一次「有請求」的時間（在記憶體中維護：偵測到 running>0 就更新 timestamp；頁面顯示「最後活動：HH:MM:SS」）、伺服器目前時間。
   - `METRICS_URL`、`PORT` 由環境變數設定，METRICS_URL 預設 `http://dgx-host:8000/metrics`。
2. **Dockerfile**：`python:3-alpine`，copy app.py，EXPOSE 8901，直接 `CMD ["python3", "app.py"]`。
3. **docker-compose.yml**：container name `dgx-in-use`，build 本目錄，ports `8901:8901`，`restart: unless-stopped`，environment 設 `METRICS_URL` 與 `PORT`。
4. **README.md**：一段話說明用途、怎麼跑、怎麼改指標來源。

程式碼註解與 README 用繁體中文（台灣用語）。

## 完成後必須驗證

1. `docker compose up -d --build`
2. `curl -s http://localhost:8901/json` 回傳合理 JSON（requests_running 應為數字）
3. `curl -s http://localhost:8901/` 的 HTML 內容應含「閒置中」或「使用中」字樣
4. 把驗證輸出貼在報告裡。
