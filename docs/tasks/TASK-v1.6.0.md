# TASK v1.6.0 — 滑鼠互動特效（桌面加強、手機零影響）

## 目標

為頁面加三個細緻的滑鼠互動特效，讓桌面瀏覽更有質感。**本任務純前端（只動 `PAGE_TMPL` 的 CSS/JS），後端與 `/json` 完全不動。**

核心原則（來自 UI polish 慣例）：
- 這頁主要在手機上看——**所有特效必須 gate 在 `@media (hover: hover) and (pointer: fine)`**，觸控裝置與窄螢幕零影響、零額外計算。
- `prefers-reduced-motion: reduce` 時停用所有位移/傾斜類特效（顏色過渡可留）。
- 只動 `transform` 與 `opacity`（GPU 加速），禁止動畫 layout 屬性；禁止 `transition: all`；時長 ≤300ms；用 `cubic-bezier(0.23, 1, 0.32, 1)` 式 ease-out。
- 追蹤滑鼠的特效**不可 1:1 直連**（很假），要用 rAF + lerp 平滑（慣性感），滑鼠離開後平滑歸位。

## 規格

### 1. 卡片微傾斜（3D tilt）

- 滑鼠在卡片上移動時，`.card` 依游標相對位置微傾：`perspective` 放在外層（body 或 card 的父級），最大 **2deg** 以內，`transform-style: preserve-3d`。
- 用 rAF + lerp（約 0.08–0.12 係數）追目標值，不可瞬跳；`pointerleave` 後平滑回到 0。
- rAF 迴圈只在需要時跑（pointer 在卡上或尚未歸位），不要常駐空轉。
- 傾斜要極微妙——質感來自「幾乎看不出來但感覺得到」。

### 2. 游標追隨光暈（spotlight）

- 新增一層 fixed 全螢幕 div（`#spotlight`，`pointer-events: none`，`z-index` 在光暈 ::before 之上、卡片之下），背景為**當前狀態色**的 radial-gradient（半徑約 260–340px），跟隨游標。
- 跟隨同樣 rAF + lerp 平滑；強度很低（中心有效不透明度 ≤0.10），是「環境感」不是「手電筒」。
- 滑鼠不在頁面上時 opacity 0（transition 0.5s ease）；狀態色切換時顏色跟著 `data-status` 走（可用 CSS 變數或 data-status 選擇器，語意色同現有四色）。
- reduced-motion 時直接不顯示（不跟隨）。

### 3. 歷史條與資訊列的 hover 回饋

- `.history-bar span`：hover 時 `transform: scaleY(1.6)`＋亮度微增（transition 150ms ease-out，`transform-origin: center bottom`），讓滑過時能看清楚那一格的顏色；既有 `title` 提示保留。
- `.info-row` 與 `.gpu-row` hover：值或名稱顏色從 `#9aa3b2` 提亮一階（color transition 150ms ease），不動 layout。
- 以上全部只在 `pointer: fine` 生效。

## 實作注意

- 全部 inline 在 `PAGE_TMPL`；**CSS/JS 內所有 `{` `}` 寫成 `{{` `}}`**；改完 JS 必跑 `node --check`（先過 `.format()` 再抽出 script）。
- JS 加在既有 IIFE 內（同層命名空間），**不得動到輪詢、frozen、FAIL_RELOAD 等既有邏輯**；spotlight 與 tilt 的初始化要容錯（找不到元素就整段跳過，不 throw）。
- JS 不得引用 `d.history` 以外的任何新資料；本任務不改 `applyData`（spotlight 顏色用 DOM data-status 讀取即可）。
- 事件用 `pointermove`/`pointerenter`/`pointerleave`；用 `matchMedia('(hover: hover) and (pointer: fine)')` 判斷是否綁事件（手機直接不綁）。
- 效能：rAF 迴圈內只寫 `transform`（spotlight 用 `translate3d`），不讀 layout（只用 `pointermove` 的 clientX/Y，不呼叫 getBoundingClientRect 於迴圈內；卡片中心可 enter 時算一次）。

## 硬性限制

1. 零外部資源；CSS/JS 全 inline。
2. 輪詢機制、`/json`、`applyData` 既有邏輯**不動**（本次純前端，後端零改動）。
3. class 語意色不變；新元素沿用同款色票。
4. 觸控裝置、窄螢幕、reduced-motion 使用者看到的頁面與 v1.5.1 完全相同（特效層不存在）。
5. 不把真實 IP 寫入 tracked 檔；不順手重構。

## 驗證步驟（輸出貼在回報最後）

1. `docker compose up -d --build`。
2. `curl -s http://localhost:8901/`：含 `#spotlight`、tilt 相關 CSS、兩個 media query gate（`hover: hover` 與 `prefers-reduced-motion`）。
3. `import app; app.render_page(...)` 四狀態 render PASS（data-status 正確、spotlight 區塊存在且不影響結構）。
4. `node --check` PASS。
5. 確認無 `transition: all`、無 layout 屬性動畫（width/height/top/margin）。

## 收尾

**不要 commit/push，回報後由 Hermes 驗收。**
