#!/usr/bin/env python3
"""dgx-in-use：顯示 TensorFold 模型 API 目前使用狀態的小型服務。

只用 Python 標準庫，每次請求時即時抓 metrics endpoint 解析指標。
"""

import html
import json
import os
import re
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


PAGE_TMPL = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="5">
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
  .gpu-fill {{ height: 100%; border-radius: 999px; background: #e6e9ef; }}
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
</style>
</head>
<body>
<div class="card">
{body}
</div>
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
            f'<div class="gpu">{gpu_rows}</div>'
            f'<div class="info">{info_rows}</div>'
        )
    else:
        body = (
            '<div class="head">DGX 模型使用狀態</div>'
            '<div class="status down"><span class="dot"></span>API 無回應</div>'
            '<div class="info">'
            + _info_row_html("連線目標", METRICS_URL, muted=True)
            + _info_row_html("伺服器時間", fmt_hms(now), muted=True)
            + "</div>"
        )

    return PAGE_TMPL.format(body=body)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/json":
            st = get_status()
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
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"dgx-in-use 啟動於 0.0.0.0:{PORT}，指標來源：{METRICS_URL}")
    server.serve_forever()
