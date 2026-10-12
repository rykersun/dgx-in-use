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
<title>DGX 模型使用狀態</title>
<style>
  body {{
    margin: 0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: -apple-system, "Noto Sans TC", sans-serif;
    background: #111;
    color: #eee;
  }}
  .card {{ text-align: center; padding: 1.5rem; }}
  .status {{ font-size: 2.2rem; font-weight: bold; line-height: 1.4; }}
  .in-use {{ color: #ff5252; }}
  .in-use-warn {{ color: #ffa726; }}
  .idle {{ color: #66bb6a; }}
  .down {{ color: #9e9e9e; }}
  .sub {{ margin-top: 1.2rem; font-size: 1rem; color: #bbb; line-height: 1.8; }}
</style>
</head>
<body>
<div class="card">
  <div class="status {cls}">{status}</div>
  <div class="sub">{sub}</div>
</div>
</body>
</html>"""


def render_page(st):
    """把狀態渲染成 zh-TW HTML 頁面。"""
    now = time.time()
    if st["ok"]:
        running, waiting = st["requests_running"], st["requests_waiting"]
        if running > 0:
            line = f"● 使用中：{running} 個請求執行中"
            if waiting > 0:
                line += f"、{waiting} 個排隊中"
            cls = "in-use-warn" if waiting > 0 else "in-use"
        else:
            line = "○ 閒置中（沒有請求）"
            cls = "idle"
        sub = []
        if isinstance(st["gpu_usage"], list):
            # 多來源模式：每個來源一行
            for item in st["gpu_usage"]:
                usage = item["usage"]
                if usage is not None:
                    sub.append(f"GPU 使用率（{item['name']}）：{usage:.0f}%")
                else:
                    sub.append(f"GPU 使用率（{item['name']}）：N/A")
        elif st["gpu_usage"] is not None:
            sub.append(f"GPU 使用率：{st['gpu_usage']:.0f}%")
        else:
            sub.append("GPU 使用率：N/A")
        if st["last_active"] is not None:
            sub.append(f"最後活動：{fmt_hms(st['last_active'])}")
        sub.append(f"伺服器時間：{fmt_hms(now)}")
        sub_html = "<br>".join(html.escape(s) for s in sub)
    else:
        line = "⚠ API 無回應"
        cls = "down"
        sub_html = f"連不上 {html.escape(METRICS_URL)}<br>伺服器時間：{fmt_hms(now)}"

    return PAGE_TMPL.format(status=html.escape(line), cls=cls, sub=sub_html)


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
