# TASK v1.3 — GPU 使用率顯示兩台節點

修改現有 dgx-in-use 專案（app.py / docker-compose.yml）。

## 背景

- 目前只顯示一台（.130）的 GPU 使用率，來源是 glances API：`GET http://dgx-host:61208/api/4/gpu`，回傳陣列，第一個元素的 `proc` 欄位即 GPU 使用率（%）。
- 第二台節點 .131 的 glances：`GET http://dgx-host-2:61208/api/4/gpu`，格式相同（已確認可從本機連線）。

## 需求

1. 支援**多個 GPU 來源**，頁面每個來源顯示一行：
   ```
   GPU 使用率（spark-23c2）：95%
   GPU 使用率（spark-6a75）：95%
   ```
2. 新環境變數 `GPU_STATS_URLS`，格式為逗號分隔的 `名稱=URL`：
   ```
   GPU_STATS_URLS=spark-23c2=http://dgx-host:61208/api/4/gpu,spark-6a75=http://dgx-host-2:61208/api/4/gpu
   ```
   - 有設 `GPU_STATS_URLS` 就用多來源模式。
   - 向下相容：若只有舊的 `GPU_STATS_URL`（單一 URL），顯示成單行「GPU 使用率：N%」。
   - 兩者皆未設時顯示 N/A。
3. 每個來源獨立抓取與容錯：某台連不上或逾時（3 秒）時該行顯示 `N/A`，不影響其他行與服務運作。可平行抓（threading）。
4. `/json` 的 `gpu_usage` 改為陣列：`[{"name": "spark-23c2", "usage": 95.0}, {"name": "spark-6a75", "usage": null}]`（單來源模式維持單一數字以相容）。

## 禁止事項

- 不要動時區、last_active 持久化、requests_running/waiting 邏輯、自動刷新、整體版面風格。

## 完成後必須驗證

1. `docker compose up -d --build`
2. `curl -s http://localhost:8901/` 應同時含「GPU 使用率（spark-23c2）」與「GPU 使用率（spark-6a75）」兩行，且數值與 `curl -s http://dgx-host{,-2}:61208/api/4/gpu` 的 proc 一致
3. `curl -s http://localhost:8901/json` 的 gpu_usage 應為兩個元素的陣列
4. 把驗證輸出貼在報告裡。
