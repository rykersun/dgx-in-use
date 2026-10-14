# CLAUDE.md

給 Claude Code 的常駐專案上下文。修改這個 repo 前先讀完本檔。

## 專案用途

顯示 DGX 叢集上 TensorFold 模型 API 目前使用狀態的小型 Docker 服務。兩人用手機瀏覽器打開頁面即可一眼判讀（使用中／閒置中／API 無回應），自動刷新由內嵌 JS 輪詢 `/json` 實現（v1.4.0 起），不得移除輪詢或改回 meta refresh，不必互相傳訊息確認。

## 架構

- `app.py`：純 Python 標準庫（`http.server`，無第三方依賴），每次請求時即時抓 metrics endpoint 解析，不做背景輪詢（v1.5.0 起的 24 小時歷史取樣 daemon thread 除外）。
- `Dockerfile`：`python:3-alpine` ＋ `apk add tzdata`（`ENV TZ=Asia/Taipei`）。
- `docker-compose.yml`：單一服務，掛載 `./data:/data` 以持久化 last_active 與歷史紀錄。

## 環境變數

| 變數 | 說明 |
|---|---|
| `PORT` | 服務埠，預設 `8901` |
| `METRICS_URL` | TensorFold 的 Prometheus 文字格式 endpoint，預設 `http://dgx-host:8000/metrics` |
| `GPU_STATS_URLS` | 多來源模式：逗號分隔的「名稱=URL」（例：`spark-23c2=http://...:61208/api/4/gpu,...`）；有設定就覆蓋 `GPU_STATS_URL` |
| `GPU_STATS_URL` | 單一 GPU 來源（向下相容；未設 `GPU_STATS_URLS` 時使用），預設 glances `http://dgx-host:61208/api/4/gpu` |
| `TZ` | 時區，固定 `Asia/Taipei` |
| `LAST_ACTIVE_FILE` | last_active 持久化路徑，預設 `/data/last_active.json` |
| `HISTORY_FILE` | 24 小時歷史紀錄持久化路徑，預設 `/data/history.json`（5 分鐘一桶、保留 25 小時） |

本機真實內網位址放在 `docker-compose.override.yml`（已 gitignore），compose 會自動疊加在 `docker-compose.yml` 之上；repo 內一律只放 `dgx-host`／`dgx-host-2` 佔位符，**不要把真實 IP 寫回 tracked 檔案**。

## 資料來源

- **TensorFold `/metrics`**：Prometheus 文字格式，取 `tensorfold:requests_running`（執行中）與 `tensorfold:requests_waiting`（排隊中）。連不上 → 頁面顯示「⚠ API 無回應」。
- **glances `/api/4/gpu`**：JSON 陣列，取第一個元素的 `proc` 欄位作為 GPU 使用率（%）。`null`／缺欄位／連不上 → 顯示 `N/A`，不影響服務。多來源模式各來源獨立容錯，平行抓取。

## 慣例

- 註解與 UI 一律用**繁體中文（台灣用語）**。
- `last_active` 以**原子寫入**（先寫 `.tmp` 再 `os.replace`）持久化到 `/data/last_active.json`；寫檔失敗只靜默忽略，**不可讓服務掛掉**。
- 時間一律以 **Asia/Taipei** 顯示。

## 驗證慣例

改動後：

```bash
docker compose up -d --build
curl http://localhost:8901/json   # 欄位正確（requests_running/waiting、gpu_usage、last_active、server_time）
curl http://localhost:8901/       # 出現中文狀態字樣（使用中／閒置中／API 無回應）
```

把兩個 curl 的輸出貼在回報裡。

## 禁止事項

- 自動刷新由內嵌 JS 輪詢 `/json` 實現（v1.4.0 起），不得移除輪詢或改回 meta refresh。
- 不破壞 last_active 持久化（原子寫入＋重啟讀回）。
- 不破壞多 GPU 多來源容錯（單一來源失敗不影響其他行）。
- 小改動不要順手重構。
