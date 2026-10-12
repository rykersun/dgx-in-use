# TASK v1.2 — 把 KV cache 使用率換成 GPU 使用率

修改現有 dgx-in-use 專案（app.py / docker-compose.yml）。

## 需求

- HTML 頁面與 `/json` 都**移除**「KV cache 使用率」，改成**「GPU 使用率」**。
- 資料來源：dgx 上現有的 glances REST API：
  - `GET http://dgx-host:61208/api/4/gpu`
  - 回傳陣列，每個元素如 `{"gpu_id": "nvidia0", "name": "NVIDIA GB10", "mem": null, "proc": 96, "temperature": 58, ...}`，`proc` 欄位就是 GPU 使用率（%）。
- 新環境變數 `GPU_STATS_URL`，預設 `http://dgx-host:61208/api/4/gpu`，寫進 docker-compose.yml。
- 解析：取第一個 GPU 的 `proc`（null 或缺欄位時顯示 N/A）；API 連不上或逾時（3 秒）顯示 N/A，不可讓服務掛掉。
- metrics 抓取（TensorFold requests_running/waiting）維持不變。

## 禁止事項

- 不要動時區、last_active 持久化、自動刷新、整體版面風格。

## 完成後必須驗證

1. `docker compose up -d --build`
2. `curl -s http://localhost:8901/` 應含「GPU 使用率：」且為數字或 N/A，不應再出現「KV cache」
3. 對照驗證：`curl -s http://dgx-host:61208/api/4/gpu` 的 proc 值與頁面顯示一致（±5 內可接受，取樣時間差）
4. `curl -s http://localhost:8901/json` 應含 gpu 欄位、不含 kv_cache_usage_ratio
5. 把驗證輸出貼在報告裡。
