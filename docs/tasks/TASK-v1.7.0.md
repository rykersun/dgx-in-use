# TASK v1.7.0 — 24 小時紀錄重新設計（48 格粗條＋使用時數統計）

## 目標

重做頁面「24 小時紀錄」區塊。現版 288 格×1px 間距太細（手機看不清、hover 連續觸發易閃），且缺「到底用了多久」的總覽數字。

**資料層完全不動**：`/json` 的 `history`（`{bucket_seconds:300, points}`）與後端取樣、持久化、修剪邏輯一律不改。本次只改**顯示層**（server render 的 `_history_block_html`／`_history_spans_html`＋前端 `updateHistory`＋CSS）。

## 新版面

```
24 小時紀錄                      使用 3.4 小時
[██░░████████░░░░░░███░░░ …]     ← 48 格、每格 30 分鐘、高 12px
00:00                                     現在
```

### 1. 48 格 × 30 分鐘粗條

- 把 `/json` 的 5 分鐘桶**聚合**成 30 分鐘槽（每槽 6 桶），槽內多狀態優先序沿用：`in-use-warn` > `in-use` > `idle` > `down`。
- `.history-bar`：height **12px**、gap **2px**、每 span `border-radius: 2px`；缺資料的槽透明佔位。手機上每格仍可辨識。
- 格數固定 **48**（24h÷30min），以 server 時鐘（JS 用 clockOffset）對齊最右槽為「目前的 30 分鐘槽」。
- 每槽 `title="HH:MM 狀態"`（HH:MM 取該槽起始時間；聚合自多桶時取優先序最高狀態的時間即可，或直接用槽起始時間——擇一，保持簡單）。
- hover 提亮（`filter: brightness(1.5)`，150ms）沿用，**不得有任何 transform 位移**（避免頻閃）。gate 在 `@media (hover: hover) and (pointer: fine)`，沿用現有結構。

### 2. 標題列：左標籤＋右統計

- 左：`24 小時紀錄`（樣式不變）。
- 右：`使用 X.X 小時`——統計 24h 內狀態為 `in-use` 與 `in-use-warn` 的桶數 ×5 分鐘。**顯示規則**：≥60 分鐘 → `使用 X.X 小時`（小數一位）；<60 分鐘 → `使用 X 分鐘`；0 分鐘 → `使用 0 分鐘`。mono 字體，樣式比照 `.history-range`。
- server 端 `render_page` 與前端 `updateHistory` **兩邊都要算**，邏輯一致。

### 3. 底部時間軸標籤

- 條下方一列：左 `HH:MM`（= now−24h，Asia/Taipei）、右 `現在`。mono、0.7rem、灰。
- server 端與前端同規則；前端每次重建時用 clockOffset 重算（每 5 分鐘桶變動時重建，標籤隨之走動即可，不需每秒更新）。
- 移除現有的 `.history-range`（右上角時間窗標籤）。

### 4. 空資料與 down

- `points` 空：顯示 `資料收集中…`（沿用現有分支）。
- down 狀態：歷史區照常顯示、統計照算。

## 硬性限制

1. 零外部資源；CSS/JS 全 inline 在 `PAGE_TMPL`。
2. 輪詢機制、`/json`、`applyData` 既有邏輯不動；後端取樣/持久化**不動**。
3. class 語意色不變（`hb-*` 沿用）；新元素沿用同款色票。
4. `PAGE_TMPL` 是 `.format()` 字串：CSS/JS 內所有 `{` `}` 寫成 `{{` `}}`；改完 JS 必跑 `node --check`（先過 `.format()` 再抽出 script）。
5. 不把真實 IP 寫入 tracked 檔；不順手重構（v1.6.3 的光暈節奏、hover 規則保留原樣）。

## 驗證步驟（輸出貼在回報最後）

1. `docker compose up -d --build`。
2. `curl -s http://localhost:8901/`：含 48 格結構、統計文字（`使用`）、底部時間標籤；無 `.history-range`。
3. `import app; app.render_page(...)` 四狀態（in-use／in-use-warn／idle／down）各驗一次；另驗空資料狀態（`資料收集中…`）與「<60 分鐘顯示分鐘、≥60 顯示小時」兩種統計文案（可用假 buckets 驗）。
4. `node --check` PASS。
5. 確認 `/json` 回應與改版前欄位完全一致（既有欄位＋history 結構不變）。

## 收尾

**不要 commit/push，回報後由 Hermes 驗收。**
