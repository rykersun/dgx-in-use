# TASK v1.4.0 — 背景輪詢刷新＋狀態驅動動態背景

## 目標

把「meta refresh 每 5 秒整頁刷新（會閃）」改成「內嵌 JS 背景輪詢 `/json`、只更新資料欄位」的無閃爍頁面，並加上狀態驅動的動態視覺（背景光暈、時間走動、使用率即時更新）。目標版本 v1.4.0。

## 設計規格

### 1. 輪詢機制（取代 meta refresh）

- **移除** `<meta http-equiv="refresh" content="5">`，改為頁尾內嵌 `<script>`（零外部資源，不新增任何 CDN/檔案）。
- 每 **5000ms** `fetch('/json')`（帶 AbortController，逾時 4 秒即視為失敗）。
- 以回應更新以下元素（**沿用既有 class 名稱與語意色**）：
  - 狀態列（`.status`）：文字＋class（`in-use`／`in-use-warn`／`idle`／`down`）。判定邏輯與後端 `render_page` 完全一致：running>0 → in-use（waiting>0 → in-use-warn）；running==0 → idle；`ok:false` → down。
  - GPU 區塊（容器加 `id="gpu"`）：`gpu_usage` 是**數字**（單來源）或**陣列**（多來源 `{name, usage}`）兩種形狀都要處理。列數或名稱改變時整個容器重建（沿用 `_gpu_row_html` 的 DOM 結構與 class）；只有數值變動時僅更新 `.gpu-usage` 文字與 `.gpu-fill` 寬度，**不要整區重繪**（避免進度條閃動）。
  - 資訊列：`last_active`（null 時該列維持後端渲染的初始狀態）、`server_time`。
  - 順帶更新 `<meta name="theme-color">` 為當前狀態色（手機狀態列跟著變）。
- **時間走動**：以每次輪詢的 `server_time` 計算 server-client 時鐘偏移，之後**每秒**用 `Intl.DateTimeFormat`（`timeZone: 'Asia/Taipei'`、`hour12: false`、HH:MM:SS）更新伺服器時間顯示。最後活動時間維持 HH:MM:SS 靜態值（輪詢到新的 last_active 才更新）。
- **失敗處理**：fetch 失敗（逾時/斷線）**不得清空畫面既有資料**；在卡片上方或狀態列下方顯示一列次要色小字「連線中斷，重試中…」（`.down` 語意色、mono 時間停格），並連續計數；**連續失敗 ≥ 6 次（約 30 秒）執行一次 `location.reload()`**（處理服務重啟/舊快取），reload 後從 0 開始。`ok:false` 是有效回應，照 down 狀態渲染，不算失敗計數。
- **省電**：`document.visibilitychange`——分頁隱藏時停止輪詢與秒針，恢復可見時立即輪詢一次並恢復計時。

### 2. 狀態驅動動態背景

在 `<body>`（或 `.card`）上以 `data-status="in-use|in-use-warn|idle|down"` 驅動：

- **使用中**：頂部紅色微光暈（radial-gradient，低不透明度），約 4s 慢速脈動。
- **排隊中**：同上但橙色。
- **閒置中**：綠色光暈，更淡更慢的呼吸感（約 6s）。
- **無回應**：無光暈（靜態灰）。
- 狀態切換時 background/box-shadow 用 `transition 0.8s ease` 平滑過渡，不要瞬跳。
- 光暈必須**低調**（off-black 底色不可被蓋成明色；光暈不透明度 ≤ 0.25）。
- **`@media (prefers-reduced-motion: reduce)`：停所有脈動/呼吸動畫，保留靜態色調**（既有 reduced-motion 區塊要涵蓋新動畫）。
- 狀態圓點既有的 pulse 動畫保留不動。

### 3. 需同步更新的檔案

- `CLAUDE.md`：「禁止事項」第一條與「專案用途」中的 meta refresh 描述改為：「自動刷新由內嵌 JS 輪詢 `/json` 實現（v1.4.0 起），**不得移除輪詢或改回 meta refresh**」。
- `app.py` 模組 docstring 與註解同步更新。

## 硬性限制（違反任一即不合格）

1. **零外部資源**：無 CDN、無 Google Fonts、無外部圖片/JS 檔；CSS/JS 全部 inline 在 `PAGE_TMPL`。
2. **`/json` API 現有欄位名稱與語意完全不變**（ok、requests_running、requests_waiting、gpu_usage、last_active、server_time）；**可以新增欄位但不可以移除或改名**。本任務原則上不需要改後端邏輯；若確有需要，只允許 additive 變更。
3. **class 名稱保留**：`in-use`／`in-use-warn`／`idle`／`down`、`gpu-row`/`gpu-head`/`gpu-name`/`gpu-usage`/`gpu-bar`/`gpu-fill`、`info-row`/`info-label`/`info-value`。
4. **last_active 原子寫入、重啟讀回**機制不動；**多 GPU 多來源容錯**不動（單一來源失敗只顯示該列 N/A）。
5. 文案繁體中文（台灣用語）；數字與時間 mono 字體；深色底維持 off-black（`#0e1014` 系）。
6. 狀態語意色固定：紅＝使用中、橙＝排隊中、綠＝閒置、灰＝無回應。
7. **不把真實 IP 寫入任何 tracked 檔案**（`docker-compose.override.yml` 除外，該檔已 gitignore）。
8. 小改動不要順手重構；`_gpu_row_html`/`_info_row_html`/後端渲染邏輯原則上不動（JS 端的 DOM 結構要與之完全一致）。

## 驗證步驟（完成後把輸出貼在回報最後）

1. `docker compose up -d --build` 成功。
2. `curl -s http://localhost:8901/json` — 確認六個欄位名稱不變。
3. **四種狀態各驗一次 render**（用假 prometheus endpoint 或 `import app; app.render_page(...)`）：
   - running=3, waiting=0 → 「使用中：3 個請求執行中」＋`in-use`
   - running=1, waiting=2 → 含「排隊中」＋`in-use-warn`
   - running=0, waiting=0 → 「閒置中」＋`idle`
   - ok=False → 「API 無回應」＋`down`
4. 每種狀態的 HTML 確認：**無 meta refresh**、含輪詢 `<script>`、`data-status` 屬性正確、reduced-motion 區塊存在。
5. JS 語法檢查：把 `<script>` 內容抽出以 `node --check` 驗證（or 至少人工逐行檢查括號與樣板字串跳脫——注意 `PAGE_TMPL` 是 `.format()` 字串，**JS 裡所有 `{` `}` 必須寫成 `{{` `}}`，這是最容易翻車的一點**）。

## 收尾

**不要 commit/push，回報後由 Hermes 驗收。**
