# dgx-in-use

一個小型 Docker 服務，即時顯示 DGX 叢集上 TensorFold 模型 API 目前有没有人在使用——兩人用手機瀏覽器打開頁面就能一眼判讀（使用中／閒置中／API 無回應），每 5 秒自動刷新，不必再互相傳訊息確認。

## 怎麼跑

```bash
docker compose up -d --build
```

然後打開 `http://<這台機器的 IP>:8901/`（手機瀏覽器即可）。另有 `http://<IP>:8901/json` 回傳 JSON，方便程式串接。

## 設定

指標來源由環境變數設定。建議建立 `docker-compose.override.yml`（已被 `.gitignore` 排除，不會提交）放入自己的環境值，`docker compose up -d` 會自動疊加在 `docker-compose.yml` 之上：

```yaml
services:
  dgx-in-use:
    environment:
      METRICS_URL: http://<跑 TensorFold 的主機>:8000/metrics
      GPU_STATS_URLS: gpu-1=http://<主機A>:61208/api/4/gpu,gpu-2=http://<主機B>:61208/api/4/gpu
```

- `METRICS_URL`：TensorFold 的 Prometheus 文字格式 endpoint（`/metrics`）。
- `GPU_STATS_URLS`：多個 glances GPU 來源，逗號分隔的「名稱=URL」；只有單一來源時可用 `GPU_STATS_URL`（單一 URL）。
- `PORT`：服務埠（預設 `8901`）。

### 佔位符對照

repo 內的佔位符對應以下主機（請換成自己的環境）：

| 佔位符 | 意義 |
|---|---|
| `dgx-host` | 跑 TensorFold 與 glances 的主機 |
| `dgx-host-2` | 第二台 GPU 節點 |

只使用 Python 標準庫，無第三方依賴。

## 版本歷史

見 [docs/tasks/](docs/tasks/)。

## 給 Claude Code

`CLAUDE.md` 說明專案慣例，修改前先讀。
