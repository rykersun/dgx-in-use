# TASK v1.5.0 — 24 小時使用歷史條

## 目標

在頁面新增「24 小時使用紀錄」橫條：一眼看出過去一天什麼時候有人用、什麼時候閒置、什麼時候 API 掛掉。

為此需要一個**架構變更（本次刻意為之）**：目前 `app.py` 是「每次請求才抓 metrics，不做背景輪詢」。歷史紀錄需要**背景取樣執行緒**定期記錄狀態，否則沒人開頁面時就沒有資料。CLAUDE.md「不做背景輪詢」的描述由本任務更新為「歷史取樣執行緒除外」。

## 資料層規格

### 背景取樣執行緒

- `__main__` 啟動時起一條 **daemon thread**，每 **60 秒**取樣一次：只抓 `fetch_metrics()`（不抓 GPU，保持輕量），分類成四狀態之一：`in-use`／`in-use-warn`／`idle`／`down`（分類邏輯與 `render_page` 一致：err→down；running>0 且 waiting>0→in-use-warn；running>0→in-use；其他→idle）。
- **整個執行緒主體包在 try/except**：任何例外只靜默略過該次取樣，絕不可讓服務掛掉。執行緒死了服務其餘功能照常（歷史只是停止更新）。
- 取樣間隔用 `threading.Event.wait(60)`（而非 `time.sleep`），以便未來可優雅停止；本次不需要 shutdown hook，daemon 隨進程結束即可。

### 儲存

- 檔案：`HISTORY_FILE` 環境變數，預設 `/data/history.json`（掛載 volume，重啟保留）。
- 格式：`{"buckets": [[unix_ts, "in-use"], ...]}`，**5 分鐘一桶**（桶 key 取樣時間整除 300 的值）。
- 同一桶多次取樣時**後寫覆蓋**；桶聚合規則（同桶多狀態時）：`in-use-warn` > `in-use` > `idle` > `down`（偏向顯示「有活動」）。
- **保留 25 小時**（顯示 24h＋緩衝），寫入前修剪超過的桶。
- **原子寫入**（`.tmp` + `os.replace`），失敗靜默忽略，與 last_active 同款慣例。每次取樣後寫一次（檔案小、60 秒一次，可接受）。
- 啟動時讀回；讀不到就從空開始。

### API

- `/json` **新增**欄位 `history`：`{"bucket_seconds": 300, "points": [[ts, "in-use"], ...]}`（僅保留最近 24h 的桶，時間正序）。
- **既有欄位一律不動**（ok/requests_running/requests_waiting/gpu_usage/last_active/server_time）。
- `ok:false`（API 無回應）時 `history` 一樣要回（歷史不會因為掛掉而消失）。
- 歷史尚未有資料時 `points` 為空陣列，**不要**省略欄位。

## UI 規格

- 位置：GPU 區塊之後、`.info` 之前，`<div class="history" id="history">`。
- 內容：
  - 標籤列：`24 小時紀錄`（左，樣式比照 `.head` 但較小或直接用 `.info-label` 風格的小字）＋右側小字顯示「最舊 HH:MM – 現在」（Asia/Taipei）。
  - 條：`.history-bar`，flex 橫條高約 8px、圓角 999px、底色 `rgba(255,255,255,0.06)`。內部每桶一個 `<span>`，寬度按比例（flex-grow），間隔 1px。顏色沿用語意色（in-use 紅 `#f87171`、in-use-warn 橙 `#fbbf24`、idle 綠 `#4ade80`、down 灰 `#9ca3af`），**透明度約 0.85**；缺資料（時間範圍內沒有桶）的 span 用透明/極暗色不佔色。
  - 每個 span 加 `title="HH:MM 狀態"`（桌面 hover 可看；手機略過不影響）。
- 空資料：顯示小字「資料收集中…」，不畫條（或畫空底條＋文字，擇一，保持簡單）。
- down 狀態時歷史區**維持顯示**（掛掉時回看歷史反而重要）；初始 render 的 down 頁也含此區。
- JS（輪詢既有機制內處理，不得新增第二個輪詢）：`applyData` 收到 `d.history` 時更新條。**只在資料簽章（桶數或任一狀態）改變時重建 innerHTML**，否則不動 DOM（避免每 5 秒重繪 288 個 span）。
- 數字／時間標籤用既有 mono 慣例；繁體中文台灣用語。

## 硬性限制

1. 零外部資源；CSS/JS 全 inline 在 `PAGE_TMPL`。
2. **輪詢機制不動**：5 秒輪詢、FAIL_RELOAD 保險、frozen 邏輯、data-status 驅動全部保留；不得改回 meta refresh。
3. `/json` 既有欄位不動（只准新增 `history`）。
4. last_active 原子寫入、多 GPU 多來源容錯不動。
5. class 名稱與語意色（紅/橙/綠/灰）不變；新增元素沿用同款色票。
6. `PAGE_TMPL` 是 `.format()` 字串：CSS/JS 內所有 `{` `}` 寫成 `{{` `}}`；改完 JS 必跑 `node --check`（抽出 script 內容驗證）。
7. 不把真實 IP 寫入 tracked 檔。
8. 不順手重構；小改動保持最小差異。

## 驗證步驟（輸出貼在回報最後）

1. `docker compose up -d --build`。
2. `curl -s http://localhost:8901/json`：既有欄位齊全＋`history.bucket_seconds==300`＋`points` 為陣列；等 60–70 秒再 curl 一次，`points` 應長出第一筆。
3. `curl -s http://localhost:8901/`：含 `history-bar` 區塊、繁中標籤、無外部資源（無 http:// 開頭的 css/js src）。
4. `import app; app.render_page(...)` 四狀態（in-use／in-use-warn／idle／down）render 各驗一次，data-status 正確且 down 頁仍含歷史區。
5. 持久化：寫入假 history.json（含舊桶）→ 重啟容器 → `points` 讀回正確且超過 25h 的桶被修剪。
6. `node --check` 抽出的 script PASS。

## 收尾

**不要 commit/push，回報後由 Hermes 驗收。**
