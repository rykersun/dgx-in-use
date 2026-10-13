# TASK v1.4.1 — 進度條動態補間＋背景光暈加強

## 目標

在 v1.4.0 的輪詢架構上（**不改輪詢邏輯**）做兩件純 CSS/微量 JS 的事：
1. GPU 進度條數值變動時**平滑補間**（寬度 transition），不要瞬跳。
2. 狀態驅動的背景光暈**加大膽**——更亮、更大、更有存在感（Jessica 明確要求「大膽一點」，v1.4.0 的 ≤0.25 不透明度限制**本次解除**，放寬到 ≤0.55）。

## 規格

### 1. 進度條補間

- `.gpu-fill` 加 `transition: width 1.4s cubic-bezier(0.22, 1, 0.36, 1)`。
- JS `patchGpu` 不用改（直接改 width，transition 自動生效）；但**整區重建**（形狀改變）時新建立的 fill 不應從 0 動畫起跳——重建後先無 transition 設定初始寬度（例如加 `no-anim` class 或直接 inline `transition:none`），下一個 frame 再恢復。
- 可選加分：數字也做短促淡入淡出（`.gpu-usage` transition color/opacity 0.3s），保持 mono。

### 2. 背景光暈加強（data-status 驅動，既有機制沿用）

- 強度：光暈有效不透明度上限放寬到 **0.55**；範圍加大（至少覆蓋視窗上半部），卡片本身也可加同色系微光 border/box-shadow（狀態色）。
- **動態感**要明顯：
  - 使用中（in-use）：紅光暈呼吸，週期約 3s，幅度要看得見（例如 opacity 在 0.25↔0.55 間擺動）。
  - 排隊中（in-use-warn）：橙光暈，同上但約 2.2s（更急促，語意「有排隊壓力」）。
  - 閒置（idle）：綠光暈慢呼吸約 7s，幅度較小（0.15↔0.35），安靜但有生命感。
  - 無回應（down）：維持靜態灰、無光暈。
- 動畫實作自選（opacity/transform/box-shadow 皆可），**避免會觸發 layout 的屬性**（不要動畫 width/height/top）。
- 狀態切換的過渡（0.8s ease）保留。
- **`prefers-reduced-motion: reduce` 時停所有脈動/呼吸動畫**，保留該狀態的靜態光暈色調——這條不可少。
- 深色底仍為 off-black 系；文字對比不可被光暈吃掉（狀態文字與數字的對比至少維持現狀）。

## 硬性限制（同 v1.4.0）

1. 零外部資源；CSS/JS 全 inline。
2. `/json` 欄位與輪詢 JS 邏輯**不動**（只動 CSS 與重建時的初始寬度處理）。
3. class 名稱與語意色（紅/橙/綠/灰）不變。
4. `PAGE_TMPL` 是 `.format()` 字串——CSS/JS 內所有 `{` `}` 寫成 `{{` `}}`，改完 `node --check` 驗證。
5. 不把真實 IP 寫入 tracked 檔；不順手重構。

## 驗證步驟（輸出貼在回報最後）

1. `docker compose up -d --build`。
2. `curl -s http://localhost:8901/` 確認：含 `.gpu-fill` transition、三種光暈 keyframes（in-use/idle/in-use-warn 各自）、reduced-motion 區塊涵蓋新動畫、無 http-equiv。
3. `import app; app.render_page(...)` 四狀態 render PASS（data-status 正確）。
4. `node --check` 抽出的 script PASS（若 JS 有動）。

## 收尾

**不要 commit/push，回報後由 Hermes 驗收。**
