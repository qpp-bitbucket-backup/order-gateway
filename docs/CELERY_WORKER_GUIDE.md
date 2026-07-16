# Celery Worker 和 QPMN 產品同步指南

## 概述

本項目使用 Celery 作為異步任務隊列，用於從 QPMN API 同步產品和 SKU 數據到數據庫。

## 架構

```
┌─────────────┐      ┌──────────┐      ┌──────────────┐
│  FastAPI    │─────▶│ RabbitMQ │─────▶│ Celery Worker│
│  (Web App)  │      │ (Broker) │      │              │
└─────────────┘      └──────────┘      └──────┬───────┘
                                              │
                                              ▼
                                     ┌────────────────┐
                                     │  QPMN API      │
                                     │  (External)    │
                                     └──────┬─────────┘
                                            │
                                            ▼
                                     ┌────────────────┐
                                     │  MySQL         │
                                     │  (Database)    │
                                     └────────────────┘
```

## 前置要求

### 1. 安裝 RabbitMQ

Celery 需要消息代理，本項目使用 RabbitMQ。在 Windows 上，你可以：

**選項 A: 使用 Docker（推薦）**
```bash
docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3-management
```

訪問管理界面：http://localhost:15672 (用戶名/密碼: guest/guest)

**選項 B: 直接安裝**
- 下載並安裝 Erlang: https://www.erlang.org/downloads
- 下載並安裝 RabbitMQ: https://www.rabbitmq.com/install-windows.html
- 啟動服務：`net start RabbitMQ`

**選項 C: 使用 WSL2/Linux**
```bash
sudo apt-get install -y erlang rabbitmq-server
sudo systemctl start rabbitmq-server
sudo rabbitmq-plugins enable rabbitmq_management
```

詳細配置指南請參考：[RABBITMQ_SETUP_GUIDE.md](RABBITMQ_SETUP_GUIDE.md)

### 2. 安裝依賴

```bash
pip install -r requirements.txt
```

### 3. 配置環境變量

編輯 `.env` 文件，確保以下配置正確：

```env
# Celery Configuration (RabbitMQ)
CELERY_BROKER_URL=amqp://guest:guest@localhost:5672//
CELERY_RESULT_BACKEND=rpc://

# QPMN API Configuration
QPMN_API_URL=https://api.qpmn.com/v1
```

## 啟動 Celery Worker

### 方法一：使用批處理腳本（推薦）

雙擊運行或在命令行執行：
```cmd
start_celery_worker.bat
```

### 方法二：手動啟動

```cmd
# 激活虛擬環境
venv\Scripts\activate

# 啟動 worker
celery -A app.core.celery.celery_app worker --loglevel=info --pool=solo
```

**注意：** Windows 上使用 `--pool=solo` 參數，因為默認的 prefork pool 不支持 Windows。

## 使用方式

### 1. 通過 API 觸發同步

#### 同步所有產品
```bash
curl -X POST "http://localhost:8000/api/sync/products" \
  -H "x-admin-key: admin-api-key-change-in-production"
```

#### 同步特定店鋪的產品
```bash
curl -X POST "http://localhost:8000/api/sync/products?store_id=STORE123" \
  -H "x-admin-key: admin-api-key-change-in-production"
```

#### 僅同步 SKU
```bash
curl -X POST "http://localhost:8000/api/sync/skus" \
  -H "x-admin-key: admin-api-key-change-in-production"
```

#### 查詢任務狀態
```bash
curl -X GET "http://localhost:8000/api/sync/status/{task_id}" \
  -H "x-admin-key: admin-api-key-change-in-production"
```

### 2. 使用 Swagger UI

訪問 http://localhost:8000/docs，找到 **Product Sync** 部分：

1. 點擊 `/api/sync/products` 或 `/api/sync/skus`
2. 點擊 "Try it out"
3. （可選）輸入 `store_id` 參數
4. 點擊 "Execute"
5. 記錄返回的 `task_id`
6. 使用 `/api/sync/status/{task_id}` 查詢任務狀態

### 3. 手動同步腳本（測試用）

```bash
# 同步所有產品
python sync_products.py

# 同步特定店鋪的產品
python sync_products.py STORE123
```

## API 端點說明

### POST /api/sync/products
觸發產品同步任務

**參數：**
- `store_id` (query, optional): 店鋪 ID，用於過濾要同步的產品

**響應：**
```json
{
  "success": true,
  "message": "Product sync started",
  "task_id": "abc123-def456-ghi789",
  "store_id": "STORE123"
}
```

### POST /api/sync/skus
觸發 SKU 同步任務

**參數：**
- `store_id` (query, optional): 店鋪 ID，用於過濾要同步的 SKU

**響應：**
```json
{
  "success": true,
  "message": "SKU sync started",
  "task_id": "abc123-def456-ghi789",
  "store_id": "STORE123"
}
```

### GET /api/sync/status/{task_id}
查詢同步任務狀態

**響應：**
```json
{
  "task_id": "abc123-def456-ghi789",
  "status": "SUCCESS",
  "result": {
    "success": true,
    "products_synced": 50,
    "skus_synced": 150,
    "timestamp": "2026-06-18T10:30:00.000Z"
  }
}
```

**可能的狀態：**
- `PENDING`: 任務等待執行
- `STARTED`: 任務正在執行
- `SUCCESS`: 任務成功完成
- `FAILURE`: 任務失敗
- `RETRY`: 任務重試中

## 任務特性

### 自動重試
- 最大重試次數：3 次
- 重試間隔：60 秒
- 超時時間：5 分鐘

### 日誌記錄
Celery worker 會輸出詳細的日誌信息：
```
[2026-06-18 10:30:00,123: INFO/MainProcess] Starting product sync from QPMN API (store_id=STORE123)
[2026-06-18 10:30:01,456: INFO/MainProcess] Fetching products from QPMN API: https://api.qpmn.com/v1
[2026-06-18 10:30:02,789: INFO/MainProcess] Product sync completed: 50 products, 150 SKUs synced
```

## 定時同步（可選）

如果需要定期自動同步，可以使用 Celery Beat。

### 1. 創建定時任務配置

在 `app/core/celery.py` 中添加：
```python
from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    'sync-products-every-hour': {
        'task': 'tasks.products.sync_products_from_qpmn',
        'schedule': crontab(minute=0),  # 每小時執行
        'args': (),
    },
}
```

### 2. 啟動 Celery Beat

```cmd
celery -A app.core.celery.celery_app beat --loglevel=info
```

## 故障排查

### 問題 1: RabbitMQ 連接失敗

**錯誤信息：**
```
kombu.exceptions.OperationalError: [Errno 111] Connection refused
```

**解決方案：**
- 確認 RabbitMQ 正在運行：`docker ps | grep rabbitmq`
- 檢查 `CELERY_BROKER_URL` 配置是否正確
- 如果使用 Docker，確認容器正在運行：`docker ps | grep rabbitmq`
- 驗證連接：`python -c "from kombu import Connection; Connection('amqp://guest:guest@localhost:5672//').connect(); print('OK')"`

### 問題 2: Worker 無法啟動

**錯誤信息：**
```
ModuleNotFoundError: No module named 'celery'
```

**解決方案：**
- 確認已安裝依賴：`pip install -r requirements.txt`
- 確認虛擬環境已激活

### 問題 3: QPMN API 請求失敗

**錯誤信息：**
```
HTTP error fetching products from QPMN: 401 Unauthorized
```

**解決方案：**
- 確認 `QPMN_API_URL` 是否正確
- 查看 QPMN API 文檔確認認證方式

### 問題 4: 任務一直處於 PENDING 狀態

**可能原因：**
- Celery worker 未啟動
- Redis 連接問題

**解決方案：**
- 確認 worker 正在運行
- 檢查 worker 日誌是否有錯誤
- 重啟 worker 和 Redis

## 監控和管理

### 查看 Worker 狀態
```bash
celery -A app.core.celery.celery_app inspect active
```

### 查看已註冊的任務
```bash
celery -A app.core.celery.celery_app inspect registered
```

### Flower - Celery Web 監控工具（可選）

安裝 Flower：
```bash
pip install flower
```

啟動 Flower：
```bash
celery -A app.core.celery.celery_app flower --port=5555
```

訪問 http://localhost:5555 查看實時監控面板。

## 最佳實踐

1. **生產環境配置：**
   - 使用獨立的 Redis 實例
   - 配置適當的 worker 數量
   - 啟用任務結果過期清理
   - 設置合理的超時和重試策略

2. **錯誤處理：**
   - 所有任務都有自動重試機制
   - 詳細的日誌記錄便於排查問題
   - 監控任務失敗率

3. **性能優化：**
   - 使用批量操作減少數據庫查詢
   - 合理設置 prefetch_multiplier
   - 考慮使用數據庫連接池

4. **安全性：**
   - 保護 ADMIN_API_KEY
   - 使用 HTTPS 連接 QPMN API
   - 定期輪換 API 密鑰

## 開發建議

1. 在開發環境中，可以手動運行同步腳本進行測試
2. 使用 Swagger UI 測試 API 端點
3. 查看 Celery worker 日誌瞭解任務執行情況
4. 定期檢查數據庫中的數據是否正確同步

## 更多信息

- [Celery 官方文檔](https://docs.celeryproject.org/)
- [Redis 官方文檔](https://redis.io/documentation)
- [FastAPI 後臺任務](https://fastapi.tiangolo.com/tutorial/background-tasks/)
