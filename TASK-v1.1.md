# TASK v1.1 — 時區與上次使用時間

針對現有 dgx-in-use 專案（app.py / Dockerfile / docker-compose.yml）做兩個修改。

## 需求 1：伺服器時間改為台灣時區

- 現況：容器內是 UTC，頁面顯示的「伺服器時間」比台灣慢 8 小時。
- 要求：所有時間顯示（伺服器時間、最後活動）一律用 **Asia/Taipei (UTC+8)**。
- 實作方式自選（建議：Dockerfile 安裝 tzdata + 設 `TZ=Asia/Taipei`，或 Python `zoneinfo`；若 alpine 缺 tzdata 會使 zoneinfo 失效，需處理）。docker-compose.yml 也要設 `TZ=Asia/Taipei` 環境變數。

## 需求 2：上次使用時間要持久化

- 現況：「最後活動」只存在記憶體，容器重啟就歸零。
- 要求：`last_active` 持久化到檔案（例如 `/data/last_active.json`），容器重啟後仍保留。
- docker-compose.yml 加 volume 掛載（`./data:/data`），app.py 在偵測到 running>0 時寫檔、啟動時讀檔。寫檔失敗不可讓服務掛掉（try/except）。
- `/json` 的 `last_active` 維持現有格式或加註時區，前端顯示維持「最後活動：HH:MM:SS」。

## 禁止事項

- 不要改動其他功能（HTML 結構、自動刷新、API 無回應處理等保持原樣）。

## 完成後必須驗證

1. `docker compose up -d --build` 重建
2. `curl -s http://localhost:8901/json` — 時間欄位應為台灣時間
3. `curl -s http://localhost:8901/` 頁面「伺服器時間」應等於 `date +%T`（本機時區）的時間 ±5 秒
4. **持久化測試**：觸發一次 running>0（可用 curl 對 dgx:8000 發一個 max_tokens 很小的請求），等 last_active 更新後 `docker compose restart`，再確認 `/json` 的 last_active 沒有變 null
5. 把驗證輸出貼在報告裡。
