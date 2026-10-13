# TASK v1.3.3 — 頁面美化

目標：把 `/` 頁面從「能看」升級成「好看好讀」，維持手機一眼判讀的定位。**只改 `app.py` 的 `PAGE_TMPL` 與 `render_page`（必要時微調 HTML 結構），不動任何抓取／解析／持久化邏輯。最後不要 commit、不要 push，回報後由 Hermes 驗收。**

## 硬性限制（違反即失敗）

- 不破壞 `<meta http-equiv="refresh" content="5">`。
- 維持純 Python 標準庫、單檔、CSS 全部 inline 在 `PAGE_TMPL`；**不引入任何外部資源**（無 CDN、無 Google Fonts、無圖片）——服務跑在內網，載入必須零外部依賴。
- 狀態語意與文案不變：使用中／排隊中／閒置中／API 無回應；`/json` endpoint 完全不動。
- 四種狀態的 class 名稱（`in-use`、`in-use-warn`、`idle`、`down`）保留，顏色語意不變（紅＝使用中、橙＝排隊、綠＝閒置、灰＝無回應）。
- 單來源與多來源 GPU 模式都要正確顯示（多來源每來源一列）。
- 註解與 UI 一律繁體中文（台灣用語）。

## 設計規格

### 色彩（深色、off-black，不用純黑）

- 頁面背景：`#0e1014`（近黑的鋅灰，非純黑）
- 卡片：`#161a22`，1px 邊框 `rgba(255,255,255,0.08)`，圓角 16px
- 狀態色（在深色底通過對比）：
  - 使用中 `#f87171`、排隊中 `#fbbf24`、閒置中 `#4ade80`、無回應 `#9ca3af`
- 次要文字 `#9aa3b2`、內文 `#e6e9ef`
- 單一強調色政策：除四個狀態色外不引入其他彩色

### 字體

- 介面：`-apple-system, "Noto Sans TC", "Microsoft JhengHei", sans-serif`
- 數字、百分比、時間一律 mono：`ui-monospace, "SF Mono", "JetBrains Mono", Consolas, monospace`

### 版面（手機優先，375px 寬為基準）

- 單欄置中卡片，`max-width: 26rem`，內距舒適（≥2rem），`min-height: 100dvh` 置中（不用 `100vh`）。
- 由上而下：
  1. 頁首小標「DGX 模型使用狀態」——小字、次要色、字距放寬（`letter-spacing`），不作為大標。
  2. 主狀態列：CSS 圓點（8px，狀態色）＋狀態文字（大、粗、狀態色）。**使用中時圓點脈動**（CSS `@keyframes`，`@media (prefers-reduced-motion: reduce)` 時關閉）；閒置／無回應時靜止。不再用 `●` `○` `⚠` 字元。
  3. GPU 區塊：每來源一列——左側來源名（小字次要色），右側使用率（mono，大）。使用率下畫 3px 高細進度條（寬度＝使用率%，底軌 `rgba(255,255,255,0.06)`，填色跟隨該列狀態無關、統一用內文色或次要色；N/A 時不畫填色、數字顯示 `N/A`）。單來源模式只一列、不顯示來源名。
  4. 資訊列：「最後活動」「伺服器時間」各一行，小字、mono 數字，標籤與值之間留白對齊，不用的 `·` 當分隔。
- 無回應狀態：主狀態「⚠ API 無回應」改為灰圓點＋「API 無回應」，下方以次要色小字顯示連線目標（`METRICS_URL`）與伺服器時間。
- 排隊中（`in-use-warn`）：主狀態文字維持「● 使用中：N 個請求執行中、M 個排隊中」語意，但圓點與文字用橙色。

### 其他

- 加 `<meta name="theme-color" content="#0e1014">`。
- 圓角一致：卡片 16px、進度條全圓角。
- 不加任何裝飾性元素（無漸層光暈、無 emoji、無版號 footer）。

## 驗證（做完自己先跑）

```bash
docker compose up -d --build
curl -s http://localhost:8901/ | head -60   # HTML 結構正確、無外部資源連結
curl -s http://localhost:8901/json          # JSON 欄位不變
```

並用 python 快速檢查四種狀態的 render：import `app` 後對四種假 `st` dict 各呼叫 `render_page`，確認 class 與文案正確（此測試腳本勿留在 repo）。

把 curl 與四狀態 render 的輸出貼在回報裡。
