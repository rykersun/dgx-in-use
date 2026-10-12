# dgx-in-use

一個小型 Docker 服務，即時顯示 DGX 叢集上 TensorFold 模型 API 目前有没有人在使用——兩人用手機瀏覽器打開頁面就能一眼判讀（使用中／閒置中／API 無回應），每 5 秒自動刷新，不必再互相傳訊息確認。

## 怎麼跑

```bash
docker compose up -d --build
```

然後打開 `http://<這台機器的 IP>:8901/`（手機瀏覽器即可）。另有 `http://<IP>:8901/json` 回傳 JSON，方便程式串接。

## 怎麼改指標來源

修改 `docker-compose.yml` 裡的 `METRICS_URL` 環境變數（預設 `http://dgx-host:8000/metrics`），再 `docker compose up -d` 重建即可；`PORT` 可改服務埠。只使用 Python 標準庫，無第三方依賴。
