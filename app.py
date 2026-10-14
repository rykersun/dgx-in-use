#!/usr/bin/env python3
"""dgx-in-use：顯示 TensorFold 模型 API 目前使用狀態的小型服務。

只用 Python 標準庫，每次請求時即時抓 metrics endpoint 解析指標。
前端頁面以內嵌 JS 每 5 秒輪詢 /json 無閃爍更新資料（v1.4.0 起，取代 meta refresh）。
另有一條背景 daemon thread 每 60 秒取樣 metrics，記錄 24 小時使用歷史（v1.5.0 起）。
"""

import html
import json
import os
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

# 由環境變數設定；METRICS_URL 指向 TensorFold 的 Prometheus 文字格式 endpoint
METRICS_URL = os.environ.get("METRICS_URL", "http://dgx-host:8000/metrics")
# GPU_STATS_URL 指向 glances 的 GPU endpoint（proc 欄位為 GPU 使用率 %）
GPU_STATS_URL = os.environ.get("GPU_STATS_URL", "http://dgx-host:61208/api/4/gpu")
# GPU_STATS_URLS 為多來源模式：逗號分隔的「名稱=URL」（有設就覆蓋 GPU_STATS_URL）
GPU_STATS_URLS = os.environ.get("GPU_STATS_URLS", "")
PORT = int(os.environ.get("PORT", "8901"))
FETCH_TIMEOUT = 3  # 抓 metrics / GPU 的逾時秒數

# last_active 持久化路徑（掛載 volume 以便重啟保留）
LAST_ACTIVE_FILE = os.environ.get("LAST_ACTIVE_FILE", "/data/last_active.json")

# 24 小時使用歷史（v1.5.0）：背景 daemon thread 每 60 秒取樣一次 metrics，
# 記錄成 5 分鐘一桶的四狀態紀錄；HISTORY_FILE 掛載 volume 以便重啟保留
HISTORY_FILE = os.environ.get("HISTORY_FILE", "/data/history.json")
HISTORY_BUCKET_SECONDS = 300  # 5 分鐘一桶（桶 key 為取樣時間整除 300）
HISTORY_RETENTION_SECONDS = 25 * 3600  # 保留 25 小時（顯示 24h＋緩衝）
HISTORY_SAMPLE_INTERVAL = 60  # 取樣間隔（秒）
# 同桶多狀態時的聚合優先序：偏向顯示「有活動」
HISTORY_STATUS_PRIORITY = ("in-use-warn", "in-use", "idle", "down")
# 各狀態的顯示用文字（span title 屬性用）
HISTORY_STATUS_TEXT = {
    "in-use": "使用中",
    "in-use-warn": "使用中（排隊中）",
    "idle": "閒置中",
    "down": "API 無回應",
}

history_buckets = {}  # {桶起始 unix_ts: 狀態}
_sample_stop = threading.Event()  # 預留未來優雅停止取樣執行緒之用

# 顯示用時區：一律 Asia/Taipei（UTC+8）；缺 tzdata 時退回系統本地時區
try:
    DISPLAY_TZ = ZoneInfo("Asia/Taipei")
except Exception:
    DISPLAY_TZ = None

# 最後一次偵測到「有請求」的時間（重啟後從 LAST_ACTIVE_FILE 讀回）
last_active_ts = None


def load_last_active():
    """啟動時從檔案讀回 last_active_ts；讀不到就維持 None。"""
    try:
        with open(LAST_ACTIVE_FILE, "r", encoding="utf-8") as f:
            ts = float(json.load(f)["last_active"])
        return ts
    except Exception:
        return None


def save_last_active(ts):
    """把 last_active_ts 寫到檔案；失敗只靜默忽略，不影響服務。"""
    try:
        d = os.path.dirname(LAST_ACTIVE_FILE)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = LAST_ACTIVE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"last_active": ts}, f)
        os.replace(tmp, LAST_ACTIVE_FILE)
    except Exception:
        pass


def fmt_hms(ts):
    """把 unix timestamp 轉成 Asia/Taipei 的 HH:MM:SS。"""
    if DISPLAY_TZ is not None:
        return datetime.fromtimestamp(ts, DISPLAY_TZ).strftime("%H:%M:%S")
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _fmt_hm(ts):
    """把 unix timestamp 轉成 Asia/Taipei 的 HH:MM（歷史橫條標籤用）。"""
    if DISPLAY_TZ is not None:
        return datetime.fromtimestamp(ts, DISPLAY_TZ).strftime("%H:%M")
    return time.strftime("%H:%M", time.localtime(ts))


def fetch_metrics():
    """抓取 METRICS_URL，回傳 (指標 dict, 錯誤訊息 or None)。"""
    try:
        with urllib.request.urlopen(METRICS_URL, timeout=FETCH_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as e:  # 連不上、逾時等各種情況都當作 API 無回應
        return None, str(e)
    return parse_prometheus(body), None


def parse_prometheus(text):
    """解析 Prometheus 文字格式，回傳 {名稱: {labels字串: 值}}。

    支援有無 labels 的行，例如：
      tensorfold:requests_running 3
      tensorfold:requests_waiting 0
    """
    metrics = {}
    line_re = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([^\s]+)\s*$')
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = line_re.match(line)
        if not m:
            continue
        name, labels, value = m.groups()
        try:
            val = float(value)
        except ValueError:
            continue  # 忽略非數值（例如 HELP/TYPE 以外的文字）
        metrics.setdefault(name, {})[labels or ""] = val
    return metrics


def fetch_gpu_usage_from(url):
    """抓取指定 glances GPU API，回傳第一個 GPU 的使用率（%）；無資料或失敗回傳 None。"""
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:  # 連不上、逾時、格式錯誤等都顯示 N/A，不讓服務掛掉
        return None
    if not isinstance(data, list) or not data:
        return None
    proc = data[0].get("proc") if isinstance(data[0], dict) else None
    if proc is None:
        return None
    try:
        return float(proc)
    except (TypeError, ValueError):
        return None


def fetch_gpu_usage():
    """單來源模式：抓取 GPU_STATS_URL 的 GPU 使用率。"""
    return fetch_gpu_usage_from(GPU_STATS_URL)


def parse_gpu_sources():
    """解析 GPU_STATS_URLS，回傳 [(名稱, URL), ...]；未設定或格式無效回傳 None。"""
    if not GPU_STATS_URLS.strip():
        return None
    sources = []
    for part in GPU_STATS_URLS.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, url = part.partition("=")
        name, url = name.strip(), url.strip()
        if name and url:
            sources.append((name, url))
    return sources or None


def fetch_gpu_usage_multi(sources):
    """多來源模式：平行抓取每個來源，回傳 [{"name": ..., "usage": ...}, ...]。

    各來源獨立容錯：某台失敗時該行 usage 為 None，不影響其他來源。
    """
    def fetch_one(url):
        return fetch_gpu_usage_from(url)

    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        usages = list(pool.map(lambda s: fetch_one(s[1]), sources))
    return [{"name": name, "usage": usage} for (name, _), usage in zip(sources, usages)]


def get_status():
    """取得目前狀態，並維護 last_active_ts。"""
    global last_active_ts
    metrics, err = fetch_metrics()
    if err is not None:
        return {"ok": False, "error": err}

    def first(name, default=0):
        """取指標的第一個值（這些指標只有一組 labels）。"""
        vals = metrics.get(name)
        if not vals:
            return default
        return next(iter(vals.values()))

    running = int(first("tensorfold:requests_running"))
    waiting = int(first("tensorfold:requests_waiting"))

    # GPU 使用率：多來源模式回傳陣列；單來源模式維持單一數字；抓不到就 N/A
    sources = parse_gpu_sources()
    if sources is not None:
        gpu_usage = fetch_gpu_usage_multi(sources)
    else:
        gpu_usage = fetch_gpu_usage()

    if running > 0:
        last_active_ts = time.time()
        save_last_active(last_active_ts)

    return {
        "ok": True,
        "requests_running": running,
        "requests_waiting": waiting,
        "gpu_usage": gpu_usage,
        "last_active": last_active_ts,
        "server_time": time.time(),
    }


def load_history():
    """啟動時從檔案讀回歷史桶；讀不到或格式錯就從空開始。"""
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            for ts, status in json.load(f)["buckets"]:
                if status in HISTORY_STATUS_TEXT:  # 擋掉損壞檔中的未知狀態
                    history_buckets[int(ts)] = status
    except Exception:
        pass


def save_history():
    """把歷史桶原子寫入檔案（.tmp + os.replace）；失敗只靜默忽略，不影響服務。"""
    try:
        d = os.path.dirname(HISTORY_FILE)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"buckets": sorted(history_buckets.items())}, f)
        os.replace(tmp, HISTORY_FILE)
    except Exception:
        pass


def record_history_sample(status):
    """記錄一次取樣：後寫覆蓋，但同桶多狀態時偏好「有活動」的狀態。

    寫入前修剪超過保留期（25 小時）的桶。
    """
    now = time.time()
    bucket = int(now) // HISTORY_BUCKET_SECONDS * HISTORY_BUCKET_SECONDS
    old = history_buckets.get(bucket)
    if old is None or HISTORY_STATUS_PRIORITY.index(status) < HISTORY_STATUS_PRIORITY.index(old):
        history_buckets[bucket] = status
    cutoff = now - HISTORY_RETENTION_SECONDS
    for ts in [ts for ts in history_buckets if ts < cutoff]:
        del history_buckets[ts]
    save_history()


def sample_history_once():
    """取樣一次：只抓 metrics（不抓 GPU），分類成四狀態（與 render_page 一致）。"""
    metrics, err = fetch_metrics()
    if err is not None:
        record_history_sample("down")
        return

    def first(name):
        vals = metrics.get(name)
        return next(iter(vals.values())) if vals else 0

    running = int(first("tensorfold:requests_running"))
    waiting = int(first("tensorfold:requests_waiting"))
    if running > 0:
        record_history_sample("in-use-warn" if waiting > 0 else "in-use")
    else:
        record_history_sample("idle")


def _history_loop():
    """背景取樣執行緒主體：任何例外只靜默略過該次取樣，絕不讓服務掛掉。"""
    while True:
        try:
            sample_history_once()
        except Exception:
            pass
        _sample_stop.wait(HISTORY_SAMPLE_INTERVAL)


def history_payload():
    """組出 /json 的 history 欄位：最近 24 小時的桶，時間正序。"""
    cutoff = time.time() - 24 * 3600
    points = [[ts, s] for ts, s in sorted(history_buckets.items()) if ts >= cutoff]
    return {"bucket_seconds": HISTORY_BUCKET_SECONDS, "points": points}


# v1.7.0 顯示層：把 5 分鐘桶聚合成 30 分鐘槽（每槽 6 桶）畫成 48 格粗條
HISTORY_SLOT_SECONDS = 30 * 60
HISTORY_SLOTS_30 = 24 * 3600 // HISTORY_SLOT_SECONDS  # 24h 橫條的格數（48）


def _fmt_usage_minutes(minutes):
    """統計文案：≥60 分鐘顯示「使用 X.X 小時」（一位小數），否則「使用 X 分鐘」。"""
    if minutes >= 60:
        return f"使用 {minutes / 60:.1f} 小時"
    return f"使用 {minutes} 分鐘"


def _history_spans_html(points, now):
    """產生 24h × 30min 的 48 格 span 列；缺資料的槽用透明 span 佔位。

    每槽聚合 6 個 5 分鐘桶，槽內多狀態沿用 HISTORY_STATUS_PRIORITY 優先序；
    title 取槽起始時間＋優先序最高的狀態。
    """
    by_ts = dict(points)
    now_slot = int(now) // HISTORY_SLOT_SECONDS * HISTORY_SLOT_SECONDS
    parts = []
    for i in range(HISTORY_SLOTS_30 - 1, -1, -1):
        slot_ts = now_slot - i * HISTORY_SLOT_SECONDS
        st = None
        for bts in range(
            slot_ts, slot_ts + HISTORY_SLOT_SECONDS, HISTORY_BUCKET_SECONDS
        ):
            s = by_ts.get(bts)
            if s is not None and (
                st is None
                or HISTORY_STATUS_PRIORITY.index(s) < HISTORY_STATUS_PRIORITY.index(st)
            ):
                st = s
        if st is None:
            parts.append("<span></span>")
        else:
            title = f"{_fmt_hm(slot_ts)} {HISTORY_STATUS_TEXT[st]}"
            parts.append(f'<span class="hb-{st}" title="{html.escape(title)}"></span>')
    return "".join(parts)


def _history_block_html(now):
    """產生整個 24 小時歷史區塊（48 格粗條＋使用時數統計＋底部時間軸）。

    尚無資料時顯示「資料收集中…」小字。統計：24h 內 in-use／in-use-warn
    桶數 ×5 分鐘（與前端 updateHistory 同規則）。
    """
    points = history_payload()["points"]
    head = '<div class="history-head"><span class="history-label">24 小時紀錄</span>'
    if not points:
        return (
            '<div class="history" id="history">' + head + "</div>"
            '<div class="history-empty">資料收集中…</div></div>'
        )
    minutes = (
        sum(1 for _, s in points if s in ("in-use", "in-use-warn"))
        * HISTORY_BUCKET_SECONDS
        // 60
    )
    head += f'<span class="history-stat">{_fmt_usage_minutes(minutes)}</span></div>'
    axis = (
        f'<div class="history-axis"><span>{_fmt_hm(now - 24 * 3600)}</span>'
        "<span>現在</span></div>"
    )
    return (
        '<div class="history" id="history">' + head
        + f'<div class="history-bar">{_history_spans_html(points, now)}</div>'
        + axis + "</div>"
    )


PAGE_TMPL = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0e1014">
<title>DGX 模型使用狀態</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    min-height: 100dvh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1.5rem;
    background: #0e1014;
    transition: box-shadow 0.8s ease;
    color: #e6e9ef;
    font-family: -apple-system, "Noto Sans TC", "Microsoft JhengHei", sans-serif;
  }}
  .card {{
    width: 100%;
    max-width: 26rem;
    background: #161a22;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 16px;
    padding: 2.5rem 2rem;
    transition: border-color 0.8s ease, box-shadow 0.8s ease;
  }}
  /* 卡片同色系微光：跟隨狀態色（down 無額外規則，維持素卡） */
  body[data-status="in-use"] .card {{
    border-color: rgba(248, 113, 113, 0.35);
    box-shadow: 0 0 60px rgba(248, 113, 113, 0.20);
  }}
  body[data-status="in-use-warn"] .card {{
    border-color: rgba(251, 191, 36, 0.35);
    box-shadow: 0 0 60px rgba(251, 191, 36, 0.20);
  }}
  body[data-status="idle"] .card {{
    border-color: rgba(74, 222, 128, 0.25);
    box-shadow: 0 0 60px rgba(74, 222, 128, 0.14);
  }}
  .head {{
    font-size: 0.8rem;
    font-weight: 500;
    color: #9aa3b2;
    letter-spacing: 0.14em;
    margin-bottom: 1.5rem;
  }}
  .status {{
    font-size: 1.5rem;
    font-weight: 700;
    line-height: 1.4;
  }}
  .dot {{
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: currentColor;
    vertical-align: middle;
    margin-right: 0.6rem;
  }}
  .in-use {{ color: #f87171; }}
  .in-use-warn {{ color: #fbbf24; }}
  .idle {{ color: #4ade80; }}
  .down {{ color: #9ca3af; }}
  /* 使用中（含排隊中）時圓點脈動；使用者偏好減少動態時關閉 */
  .in-use .dot, .in-use-warn .dot {{ animation: pulse 1.6s ease-in-out infinite; }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.3; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .in-use .dot, .in-use-warn .dot {{ animation: none; }}
    body[data-status]::before {{ animation: none; }}  /* 停光暈脈動，保留靜態色調 */
  }}
  /* 狀態驅動光暈：::before 為脈動光暈層（radial 中心在卡片後方，形成環繞光暈而非僅頂部），body inset box-shadow 為四邊環境光（切換時 0.8s 過渡）；
     有效不透明度上限 0.55（v1.4.1 放寬）。規則內的 opacity 為 reduced-motion 停用動畫時的靜態色調。 */
  body[data-status="in-use"] {{ box-shadow: inset 0 0 220px -90px rgba(248, 113, 113, 0.14); }}
  body[data-status="in-use-warn"] {{ box-shadow: inset 0 0 220px -90px rgba(251, 191, 36, 0.14); }}
  body[data-status="idle"] {{ box-shadow: inset 0 0 220px -90px rgba(74, 222, 128, 0.10); }}
  body::before {{
    content: "";
    position: fixed;
    inset: 0;
    z-index: -1;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.8s ease;
  }}
  body[data-status="in-use"]::before {{
    background: radial-gradient(120% 90% at 50% 45%, rgba(248, 113, 113, 1), rgba(248, 113, 113, 0) 85%);
    opacity: 0.2;
    animation: glow-in-use 1.8s ease-in-out infinite;  /* 使用中：急促呼吸 */
  }}
  body[data-status="in-use-warn"]::before {{
    background: radial-gradient(120% 90% at 50% 45%, rgba(251, 191, 36, 1), rgba(251, 191, 36, 0) 85%);
    opacity: 0.2;
    animation: glow-warn 1.4s ease-in-out infinite;  /* 排隊中：更急促 */
  }}
  body[data-status="idle"]::before {{
    background: radial-gradient(120% 90% at 50% 45%, rgba(74, 222, 128, 1), rgba(74, 222, 128, 0) 85%);
    opacity: 0.12;
    animation: glow-idle 9s ease-in-out infinite;  /* 閒置中：非常緩慢 */
  }}
  /* 無回應（down）：無光暈，維持靜態灰 */
  @keyframes glow-in-use {{
    0%, 100% {{ opacity: 0.12; }}
    50% {{ opacity: 0.30; }}
  }}
  @keyframes glow-warn {{
    0%, 100% {{ opacity: 0.12; }}
    50% {{ opacity: 0.30; }}
  }}
  @keyframes glow-idle {{
    0%, 100% {{ opacity: 0.08; }}
    50% {{ opacity: 0.20; }}
  }}
  .gpu {{ margin-top: 1.75rem; }}
  .gpu-row + .gpu-row {{ margin-top: 1.1rem; }}
  .gpu-head {{ display: flex; align-items: baseline; gap: 1rem; }}
  .gpu-name {{ font-size: 0.85rem; color: #9aa3b2; }}
  .gpu-usage {{
    margin-left: auto;
    font-family: ui-monospace, "SF Mono", "JetBrains Mono", Consolas, monospace;
    font-size: 1.4rem;
    font-weight: 600;
  }}
  .gpu-bar {{
    height: 3px;
    margin-top: 0.55rem;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.06);
    overflow: hidden;
  }}
  .gpu-fill {{
    height: 100%;
    border-radius: 999px;
    background: #e6e9ef;
    transition: width 1.4s cubic-bezier(0.22, 1, 0.36, 1);
  }}
  /* 整區重建時新 fill 先免動畫設初始寬度，下一個 frame 由 JS 移除此 class */
  .gpu-fill.no-anim {{ transition: none; }}
  /* 24 小時使用紀錄（v1.7.0）：5 分鐘桶聚合成 48 格 ×30 分鐘粗條＋使用時數統計 */
  .history {{ margin-top: 1.75rem; }}
  .history-head {{
    display: flex;
    align-items: baseline;
    gap: 1rem;
    margin-bottom: 0.55rem;
  }}
  .history-label {{ font-size: 0.85rem; color: #9aa3b2; }}
  .history-stat {{
    margin-left: auto;
    font-family: ui-monospace, "SF Mono", "JetBrains Mono", Consolas, monospace;
    font-size: 0.75rem;
    color: #9aa3b2;
  }}
  .history-bar {{
    display: flex;
    gap: 2px;
    height: 12px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.06);
    overflow: hidden;
  }}
  .history-bar span {{ flex: 1 1 0; min-width: 0; border-radius: 2px; }}
  .history-axis {{
    display: flex;
    justify-content: space-between;
    margin-top: 0.4rem;
    font-family: ui-monospace, "SF Mono", "JetBrains Mono", Consolas, monospace;
    font-size: 0.7rem;
    color: #9aa3b2;
  }}
  .hb-in-use {{ background: rgba(248, 113, 113, 0.85); }}
  .hb-in-use-warn {{ background: rgba(251, 191, 36, 0.85); }}
  .hb-idle {{ background: rgba(74, 222, 128, 0.85); }}
  .hb-down {{ background: rgba(156, 163, 175, 0.85); }}
  .history-empty {{ font-size: 0.75rem; color: #9aa3b2; }}
  .info {{ margin-top: 1.75rem; }}
  .info-row {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 1rem;
    font-size: 0.85rem;
    line-height: 1.9;
  }}
  .info-label {{ color: #9aa3b2; flex-shrink: 0; }}
  .info-value {{
    font-family: ui-monospace, "SF Mono", "JetBrains Mono", Consolas, monospace;
    color: #e6e9ef;
    text-align: right;
    word-break: break-all;
  }}
  /* 無回應狀態：資訊值改用次要色 */
  .info-value.muted {{ color: #9aa3b2; }}
  /* 連線中斷提示（輪詢失敗時顯示，.down 語意色） */
  .conn {{
    margin-top: 0.5rem;
    font-size: 0.75rem;
    color: #9ca3af;
  }}
  /* ── v1.7.0 起移除游標追隨光暈；保留桌面 hover 回饋 ──
     全部 gate 在桌面精準指標裝置（hover: hover + pointer: fine），
     觸控裝置與窄螢幕零影響、零額外計算。 */
  @media (hover: hover) and (pointer: fine) {{
    /* 歷史條 hover 只提亮不變形：位移式 hover 在多格上連續觸發會像頻閃 */
    .history-bar span {{
      transition: filter 150ms cubic-bezier(0.23, 1, 0.32, 1);
    }}
    .history-bar span:hover {{
      filter: brightness(1.5);
    }}
    .info-row .info-label,
    .info-row .info-value.muted,
    .gpu-row .gpu-name {{ transition: color 150ms ease; }}
    .info-row:hover .info-label,
    .info-row:hover .info-value.muted,
    .gpu-row:hover .gpu-name {{ color: #c9d1de; }}
  }}
</style>
</head>
<body data-status="{status}">
<div class="card">
{body}
</div>
<script>
(function () {{
  "use strict";
  var POLL_MS = 5000;        // 輪詢間隔（毫秒）
  var FETCH_TIMEOUT = 4000;  // 單次請求逾時
  var FAIL_RELOAD = 6;       // 連續失敗達此次數就整頁重載
  var STATUS_COLORS = {{
    "in-use": "#f87171",
    "in-use-warn": "#fbbf24",
    "idle": "#4ade80",
    "down": "#9ca3af"
  }};

  var statusEl = document.querySelector(".status");
  var connEl = document.getElementById("conn");
  var gpuEl = document.getElementById("gpu");
  var historyEl = document.getElementById("history");
  var themeEl = document.querySelector('meta[name="theme-color"]');

  var failCount = 0;
  var frozen = false;        // 斷線中：時間停格
  var clockOffset = null;    // server-client 時鐘偏移（毫秒）
  var lastGpuShape = null;   // 上次 GPU 區塊形狀（列數／名稱／是否 N/A）
  var lastHistorySig = null; // 上次歷史資料簽章（變了才重建 DOM）
  var HISTORY_SLOTS = 48;        // 24h ÷ 30min 的格數（v1.7.0 粗條）
  var HISTORY_SLOT_SECONDS = 1800;  // 每格 30 分鐘（聚合 6 個 5 分鐘桶）
  var HISTORY_BUCKET_SECONDS = 300;  // 後端資料桶寬（秒）
  var HISTORY_CLASSES = {{
    "in-use": "hb-in-use",
    "in-use-warn": "hb-in-use-warn",
    "idle": "hb-idle",
    "down": "hb-down"
  }};
  var HISTORY_TEXT = {{
    "in-use": "使用中",
    "in-use-warn": "使用中（排隊中）",
    "idle": "閒置中",
    "down": "API 無回應"
  }};

  var timeFmt = null;
  try {{
    timeFmt = new Intl.DateTimeFormat("zh-TW", {{
      timeZone: "Asia/Taipei",
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    }});
  }} catch (err) {{
    timeFmt = null;  // 無該時區資料時改用 ISO fallback
  }}

  function fmtHms(ms) {{
    if (timeFmt) return timeFmt.format(new Date(ms));
    return new Date(ms + 8 * 3600000).toISOString().slice(11, 19);
  }}

  function escapeHtml(s) {{
    var map = {{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }};
    return String(s).replace(/[&<>"']/g, function (c) {{ return map[c]; }});
  }}

  function statusInfo(d) {{
    // 判定邏輯與後端 render_page 完全一致
    if (!d.ok) return {{ cls: "down", text: "API 無回應" }};
    if (d.requests_running > 0) {{
      var text = "使用中：" + d.requests_running + " 個請求執行中";
      if (d.requests_waiting > 0) text += "、" + d.requests_waiting + " 個排隊中";
      return {{ cls: d.requests_waiting > 0 ? "in-use-warn" : "in-use", text: text }};
    }}
    return {{ cls: "idle", text: "閒置中（沒有請求）" }};
  }}

  function infoRow(label) {{
    var rows = document.querySelectorAll(".info-row");
    for (var i = 0; i < rows.length; i++) {{
      var lab = rows[i].querySelector(".info-label");
      if (lab && lab.textContent === label) return rows[i];
    }}
    return null;
  }}

  function gpuItems(d) {{
    // gpu_usage 單來源是數字、多來源是 [{{name, usage}}]，統一成陣列處理
    if (Array.isArray(d.gpu_usage)) return d.gpu_usage;
    return [{{ name: null, usage: d.gpu_usage }}];
  }}

  function gpuShape(items) {{
    // 列數、來源名、是否 N/A 任一改變就整區重建
    return items.map(function (it) {{
      return (it.name == null ? "" : it.name) + ":" + (it.usage == null ? "NA" : "num");
    }}).join("|");
  }}

  function gpuRowHtml(name, usage) {{
    // 與後端 _gpu_row_html 的 DOM 結構與 class 完全一致
    var nameHtml = name ? '<span class="gpu-name">' + escapeHtml(name) + "</span>" : "";
    var valHtml = "N/A";
    var fillHtml = "";
    if (usage != null) {{
      valHtml = Math.round(usage) + "%";
      var width = Math.max(0, Math.min(100, usage));
      fillHtml = '<div class="gpu-fill no-anim" style="width:' + Math.round(width) + '%"></div>';
    }}
    return '<div class="gpu-row"><div class="gpu-head">' + nameHtml +
      '<span class="gpu-usage">' + valHtml + "</span></div>" +
      '<div class="gpu-bar">' + fillHtml + "</div></div>";
  }}

  function patchGpu(items) {{
    // 只有數值變動：只改文字與進度條寬度，不整區重繪
    var rows = gpuEl.querySelectorAll(".gpu-row");
    for (var i = 0; i < rows.length && i < items.length; i++) {{
      var usage = items[i].usage;
      var valEl = rows[i].querySelector(".gpu-usage");
      var fillEl = rows[i].querySelector(".gpu-fill");
      if (usage == null) {{
        valEl.textContent = "N/A";
      }} else {{
        valEl.textContent = Math.round(usage) + "%";
        if (fillEl) fillEl.style.width = Math.max(0, Math.min(100, usage)) + "%";
      }}
    }}
  }}

  function updateHistory(points) {{
    // 只在資料簽章（桶數或任一狀態）改變時重建 innerHTML，避免每 5 秒重繪 48 個 span
    var sig = points.map(function (p) {{ return p.join(":"); }}).join("|");
    if (sig === lastHistorySig) return;
    lastHistorySig = sig;
    var head = '<div class="history-head"><span class="history-label">24 小時紀錄</span>';
    if (!points.length) {{
      historyEl.innerHTML = head + '</div><div class="history-empty">資料收集中…</div>';
      return;
    }}
    // 槽內多狀態聚合優先序：與後端 HISTORY_STATUS_PRIORITY 一致
    var PRIORITY = ["in-use-warn", "in-use", "idle", "down"];
    var byTs = {{}};
    for (var i = 0; i < points.length; i++) byTs[points[i][0]] = points[i][1];
    // 以 server 時鐘（clockOffset）對齊目前的 30 分鐘槽
    var nowMs = clockOffset == null ? Date.now() : Date.now() + clockOffset;
    var nowSlot = Math.floor(nowMs / 1000 / HISTORY_SLOT_SECONDS) * HISTORY_SLOT_SECONDS;
    var spans = "";
    for (var i = HISTORY_SLOTS - 1; i >= 0; i--) {{
      var slotTs = nowSlot - i * HISTORY_SLOT_SECONDS;
      var st = null;
      for (var bts = slotTs; bts < slotTs + HISTORY_SLOT_SECONDS; bts += HISTORY_BUCKET_SECONDS) {{
        var s = byTs[bts];
        if (s && (st === null || PRIORITY.indexOf(s) < PRIORITY.indexOf(st))) st = s;
      }}
      if (st) {{
        spans += '<span class="' + HISTORY_CLASSES[st] + '" title="' +
          fmtHms(slotTs * 1000).slice(0, 5) + " " + HISTORY_TEXT[st] + '"></span>';
      }} else {{
        spans += "<span></span>";
      }}
    }}
    // 統計：24h 內 in-use／in-use-warn 桶數 ×5 分鐘（與後端 _history_block_html 同規則）
    var minutes = 0;
    for (var i = 0; i < points.length; i++) {{
      var s2 = points[i][1];
      if (s2 === "in-use" || s2 === "in-use-warn") minutes += HISTORY_BUCKET_SECONDS / 60;
    }}
    var stat = minutes >= 60
      ? "使用 " + (minutes / 60).toFixed(1) + " 小時"
      : "使用 " + minutes + " 分鐘";
    historyEl.innerHTML = head +
      '<span class="history-stat">' + stat + "</span></div>" +
      '<div class="history-bar">' + spans + "</div>" +
      '<div class="history-axis"><span>' + fmtHms(nowMs - 24 * 3600 * 1000).slice(0, 5) +
      "</span><span>現在</span></div>";
  }}

  function applyData(d) {{
    var st = statusInfo(d);
    document.body.setAttribute("data-status", st.cls);
    if (themeEl) themeEl.setAttribute("content", STATUS_COLORS[st.cls]);
    if (statusEl) {{
      statusEl.className = "status " + st.cls;
      statusEl.innerHTML = '<span class="dot"></span>' + escapeHtml(st.text);
    }}

    if (d.ok) {{
      // GPU 區塊（down 頁沒有此容器，回復時動態補上）
      if (!gpuEl) {{
        gpuEl = document.createElement("div");
        gpuEl.id = "gpu";
        gpuEl.className = "gpu";
        var infoSec = document.querySelector(".info");
        infoSec.parentNode.insertBefore(gpuEl, infoSec);
      }}
      gpuEl.style.display = "";
      var items = gpuItems(d);
      var shape = gpuShape(items);
      if (shape !== lastGpuShape) {{
        gpuEl.innerHTML = items.map(function (it) {{ return gpuRowHtml(it.name, it.usage); }}).join("");
        lastGpuShape = shape;
        // 重建的新 fill 不從 0 動畫起跳：先無 transition 設定初始寬度，下一個 frame 再恢復
        requestAnimationFrame(function () {{
          var fills = gpuEl.querySelectorAll(".gpu-fill.no-anim");
          for (var i = 0; i < fills.length; i++) fills[i].classList.remove("no-anim");
        }});
      }} else {{
        patchGpu(items);
      }}

      // 最後活動：後端初始渲染沒有此列（last_active 為 null）時動態補上
      if (d.last_active != null) {{
        var laRow = infoRow("最後活動");
        if (!laRow) {{
          laRow = document.createElement("div");
          laRow.className = "info-row";
          laRow.innerHTML = '<span class="info-label">最後活動</span><span class="info-value"></span>';
          var stRow = infoRow("伺服器時間");
          stRow.parentNode.insertBefore(laRow, stRow);
        }}
        laRow.style.display = "";
        laRow.querySelector(".info-value").textContent = fmtHms(d.last_active * 1000);
      }}
    }} else {{
      // 無回應：與後端 down 渲染一致，收起 GPU 區塊與最後活動列
      if (gpuEl) gpuEl.style.display = "none";
      var laRow2 = infoRow("最後活動");
      if (laRow2) laRow2.style.display = "none";
    }}

    // 伺服器時間：記下時鐘偏移並立即更新一次
    if (typeof d.server_time === "number") {{
      clockOffset = d.server_time * 1000 - Date.now();
      var nowRow = infoRow("伺服器時間");
      if (nowRow) nowRow.querySelector(".info-value").textContent = fmtHms(d.server_time * 1000);
    }}

    // 24 小時歷史（down 時歷史照常回，維持顯示）
    if (historyEl && d.history && Array.isArray(d.history.points)) {{
      updateHistory(d.history.points);
    }}
  }}

  function poll() {{
    var ctrl = new AbortController();
    var timer = setTimeout(function () {{ ctrl.abort(); }}, FETCH_TIMEOUT);
    fetch("/json", {{ cache: "no-store", signal: ctrl.signal }})
      .then(function (r) {{ return r.json(); }})
      .then(function (d) {{
        clearTimeout(timer);
        failCount = 0;
        frozen = false;
        if (connEl) connEl.hidden = true;
        applyData(d);
      }})
      .catch(function () {{
        clearTimeout(timer);
        failCount += 1;
        frozen = true;  // 時間停格，不清空畫面既有資料
        if (connEl) connEl.hidden = false;
        if (failCount >= FAIL_RELOAD) location.reload();
      }});
  }}

  function tickClock() {{
    if (document.hidden || frozen || clockOffset == null) return;
    var row = infoRow("伺服器時間");
    if (row) {{
      var v = row.querySelector(".info-value");
      if (v) v.textContent = fmtHms(Date.now() + clockOffset);
    }}
  }}

  function loop() {{
    if (!document.hidden) poll();  // 分頁隱藏時暫停輪詢
  }}

  setInterval(loop, POLL_MS);
  setInterval(tickClock, 1000);
  document.addEventListener("visibilitychange", function () {{
    if (!document.hidden) loop();  // 恢復可見立即輪詢一次
  }});

  loop();  // 載入立即抓一次，同步時鐘
}})();
</script>
</body>
</html>"""


def _gpu_row_html(name, usage):
    """產生單一 GPU 來源的列 HTML；name 為 None（單來源模式）不顯示來源名。

    usage 為 None 時顯示 N/A 且不畫進度條填色。
    """
    name_html = f'<span class="gpu-name">{html.escape(name)}</span>' if name else ""
    if usage is None:
        val_html, fill_html = "N/A", ""
    else:
        val_html = f"{usage:.0f}%"
        width = max(0.0, min(100.0, usage))
        fill_html = f'<div class="gpu-fill" style="width:{width:.0f}%"></div>'
    return (
        '<div class="gpu-row">'
        f'<div class="gpu-head">{name_html}<span class="gpu-usage">{val_html}</span></div>'
        f'<div class="gpu-bar">{fill_html}</div>'
        "</div>"
    )


def _info_row_html(label, value, muted=False):
    """產生「標籤 + 值」的資訊列 HTML。"""
    muted_cls = " muted" if muted else ""
    return (
        '<div class="info-row">'
        f'<span class="info-label">{html.escape(label)}</span>'
        f'<span class="info-value{muted_cls}">{html.escape(value)}</span>'
        "</div>"
    )


def render_page(st):
    """把狀態渲染成 zh-TW HTML 頁面。"""
    now = time.time()
    if st["ok"]:
        running, waiting = st["requests_running"], st["requests_waiting"]
        if running > 0:
            line = f"使用中：{running} 個請求執行中"
            if waiting > 0:
                line += f"、{waiting} 個排隊中"
            cls = "in-use-warn" if waiting > 0 else "in-use"
        else:
            line = "閒置中（沒有請求）"
            cls = "idle"
        # GPU 區塊：多來源模式每來源一列；單來源模式一列、不顯示來源名
        if isinstance(st["gpu_usage"], list):
            gpu_rows = "".join(_gpu_row_html(item["name"], item["usage"]) for item in st["gpu_usage"])
        else:
            gpu_rows = _gpu_row_html(None, st["gpu_usage"])
        info_rows = ""
        if st["last_active"] is not None:
            info_rows += _info_row_html("最後活動", fmt_hms(st["last_active"]))
        info_rows += _info_row_html("伺服器時間", fmt_hms(now))
        body = (
            '<div class="head">DGX 模型使用狀態</div>'
            f'<div class="status {cls}"><span class="dot"></span>{html.escape(line)}</div>'
            '<div class="conn" id="conn" hidden>連線中斷，重試中…</div>'
            f'<div class="gpu" id="gpu">{gpu_rows}</div>'
            + _history_block_html(now)
            + f'<div class="info">{info_rows}</div>'
        )
    else:
        cls = "down"
        body = (
            '<div class="head">DGX 模型使用狀態</div>'
            '<div class="status down"><span class="dot"></span>API 無回應</div>'
            '<div class="conn" id="conn" hidden>連線中斷，重試中…</div>'
            + _history_block_html(now)
            + '<div class="info">'
            + _info_row_html("連線目標", METRICS_URL, muted=True)
            + _info_row_html("伺服器時間", fmt_hms(now), muted=True)
            + "</div>"
        )

    return PAGE_TMPL.format(body=body, status=cls)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/json":
            st = get_status()
            st["history"] = history_payload()  # 既有欄位不動，只新增 history
            body = json.dumps(st, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        elif self.path == "/":
            st = get_status()
            body = render_page(st).encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
        else:
            body = b"Not Found\n"
            self._send(404, body, "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        pass  # 安靜模式：不印存取日誌


if __name__ == "__main__":
    last_active_ts = load_last_active()
    load_history()
    threading.Thread(target=_history_loop, daemon=True).start()  # 歷史取樣，隨進程結束
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"dgx-in-use 啟動於 0.0.0.0:{PORT}，指標來源：{METRICS_URL}")
    server.serve_forever()
