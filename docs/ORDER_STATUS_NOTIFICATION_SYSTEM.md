# 訂單狀態變更通知系統(級別標記 + Client 級郵件通知)變更說明

## 概述

本變更為訂單引進一套**按級別(level)的狀態變更通知系統**:

1. **按級別記錄所有狀態變更**:每次訂單狀態轉換都會在 `order.logs` 寫入一條帶 `level` 標籤(info / warning / error)的日誌條目,統一經由 `record_status_change()` 入口完成。
2. **Client 級郵件通知配置**:每個 client(店鋪)可透過 `notification_config` 設定各級別是否啟用郵件通知及收件人清單;狀態變更時,該級別啟用的收件人會收到 SendGrid 通知郵件。

- **向後兼容**:未配置 `notification_config` 的 client(NULL)全部級別停用,行為與變更前完全一致——通知任務只會把發送記錄標記為 `skipped`。
- **發送非同步、可審計、可重試**:每次狀態變更產生一條 `notification_email_logs` 記錄(模式同 OMS/VFS 的 outbound `WebhookLog`),發送失敗以指數退避重試。

## 級別體系

| 級別 | 含義 | 涵蓋狀態 |
|------|------|----------|
| `info` | 正常流轉 | received、pending、validated、cooling_off、processing、printready、printed、produced、shipped |
| `warning` | 需關注 | cancelled |
| `error` | 失敗/異常,通常需人工介入 | failed、errored |

映射定義於 [`STATUS_NOTIFICATION_LEVEL`](../app/models/notification.py)(未映射的未來狀態回退為 `info`;測試斷言全覆蓋,新增狀態必須顯式決定級別)。

**郵件靜音**:[`EMAIL_MUTED_STATUSES`](../app/models/notification.py)(`PENDING`、`PROCESSING`)——這兩個高頻中間狀態不派發郵件(`enqueue_status_change_email()` 在寫庫前直接返回 None),但 `order.logs` 仍照常記錄;`COOLING_OFF` 不在靜音集,進入冷靜期會通知(郵件附帶冷靜期時長)。

## 核心流程

```text
訂單狀態變更(10 處賦值點)
    │
    ▼
record_status_change()          統一入口:設定新狀態 + order.logs 追加帶 level 的條目
    │                            (不 commit,呼叫端保持原有 add/commit 流程)
    ▼ (caller commit 後)
enqueue_status_change_email()   進入 PENDING/PROCESSING(EMAIL_MUTED_STATUSES)→ 直接返回,不建行不派發
    │                            否則:建立 NotificationEmailLog(status=received)+ dispatch 任務
    │
    ▼
notify_order_status_email 任務   查 client.notification_config → 解析該級別收件人
    ├─ 級別未啟用/未配置/無郵箱 → skipped
    ├─ SENDGRID_API_KEY 未配置  → skipped
    ├─ 全部發送成功             → processed
    └─ 有發送失敗               → 指數退避重試(EMAIL_NOTIFY_RETRY_*),耗盡 → failed
```

- 入口與派發邏輯:[app/services/order_notifications.py](../app/services/order_notifications.py)
- Celery 任務:[app/tasks/notifications.py](../app/tasks/notifications.py)(`tasks.notifications.notify_order_status_email`,走既有 `order_notifying` 佇列,無需新消費者)
- 重試策略:一輪內任一收件人失敗即整封重試(重試可能對已成功地址重發——對 WARNING/ERROR 告警,重複優於丟失),重試簿記模式與 `notify_oms`/`notify_vfs` 一致(記錄行上的 `retry_count` + 手動 `apply_async`)。

## order.logs 條目格式

**所有新寫入的條目均攜帶 `level` 欄位**，且統一經由 [`OrderService.append_log`](../app/services/order.py)（唯一寫入路徑）：狀態變更條目由 `record_status_change()` 按目標狀態解析級別後調用它；非狀態變更條目（retry、水印、條碼、地址更新、平台操作記錄等）預設 `"info"`（`level` 參數可供覆蓋；`worker`/`extra` 可選）。tasks 層的 `_append_order_log` 為其薄包裝（懶導入避開 services↔tasks 循環依賴）。歷史舊條目無 `level`，讀取端仍需容忍缺省：

```json
{
  "timestamp": "2026-09-14T02:30:00+00:00",
  "action": "order_push_failed",
  "message": "QPMN returned success=false: ...",
  "level": "error",
  "worker": "celery_worker"
}
```

## 資料庫變更

遷移檔:[`d5e6f7a8b9c0_create_notification_email_logs_and_client_notification_config.py`](../alembic/versions/2026_09_14_1030-d5e6f7a8b9c0_create_notification_email_logs_and_client_notification_config.py)(down_revision = `c9d0e1f2a3b4`)

| 變更 | 表 | 說明 |
|------|-----|------|
| 新增欄位 | `clients.notification_config` | `JSON NULL`,每級別設定:`{"info"\|"warning"\|"error": {"enabled": bool, "emails": [...]}}`;NULL = 全部停用 |
| 新建表 | `notification_email_logs` | 每次狀態變更一行的外發郵件記錄,欄位:`order_id`/`source_order_id`/`store_id`/`from_status`/`to_status`/`level`/`message`/`recipients`(任務回填)/`process_status`(received/processed/skipped/failed)/`details`/`retry_count` + BaseModel 標準欄位;索引:`order_id`、`store_id`、`to_status`、`level`、`process_status`、`(order_id, created_at)` |

## Client API 變更([app/api/clients.py](../app/api/clients.py)、[app/schemas/client.py](../app/schemas/client.py))

`ClientCreateRequest` / `PlatformClientCreateRequest` / `ClientUpdateRequest` / `ClientSummary` 均新增 `notification_config` 欄位(巢狀 `NotificationConfig`:`info`/`warning`/`error` → `{enabled, emails}`),郵箱以 `EmailStr` 校驗;管理端與平台端的建立/更新/摘要接口全部支援讀寫。

配置示例:

```json
PUT /platform/clients/{client_id}
{
  "notification_config": {
    "info":    {"enabled": false, "emails": []},
    "warning": {"enabled": true,  "emails": ["cs@example.com"]},
    "error":   {"enabled": true,  "emails": ["ops@example.com", "alert@example.com"]}
  }
}
```

## 通知郵件

由 [`render_order_status_email`](../app/services/email_templates.py) 內置渲染(零外部配置),複用既有的品牌郵件外殼(`_render_email_shell`),所有值經 HTML escape:

- **頭部背景色**：郵件頭部「QPMN Order Gateway」的背景色依級別變化：info 藍(#0842a0)、warning 褐(#bf8e24)、error 深紅(#c10d0d)；未知級別回退 info 藍。其他郵件(如密碼重設)不受影響，保持品牌深藍(#1a1a2e)
- **主題**:`[LEVEL] Order {source_order_id} {Status}` 風格,優先使用外部可辨識的 `source_order_id`(缺省回退內部 `order_id`),目標狀態首字母大寫(`cooling_off` → `Cooling Off`),例如 `[ERROR] Order SHOP-2026-1001 Failed`;新建訂單顯示為 `[INFO] Order SHOP-2026-1001 Received`
- **正文**:表格呈現 Order ID、Source Order ID、Store(顯示 client 名稱,查不到時回退 store_id)、狀態變更(`new -> received` 風格)、冷靜期時長(僅 COOLING_OFF 通知且 `order.cooling_off_seconds` 有值時顯示,如「2 days」、「1 hour 30 minutes」)、級別、觸發訊息、發生時間(UTC),同時提供純文字版
- **查看詳細按鈕**:配置 `ADMIN_BASE_URL` 後,HTML 版附「View Details」按鈕連結至 `{ADMIN_BASE_URL}/admin/orders/show/{order_id}`(內部 order_id,管理後台路由用),樣式與密碼重設郵件按鈕配方一致(靛藍 #4f46e5、圓角 4px 的 inline-block `<a>`);純文字版附對應連結行;未配置則不渲染

## 狀態變更接入點(11 處)

| 檔案 | 狀態 | 說明 |
|------|------|------|
| [app/tasks/orders.py](../app/tasks/orders.py) | PENDING / VALIDATED / COOLING_OFF / PROCESSING / FAILED×2 | publish、validate、cooling-off、push 成功、push 業務失敗、`_mark_order_failed`;VALIDATED 原本無 log,本次補上 `order_validated` 條目 |
| [app/services/order.py](../app/services/order.py) | CANCELLED / RECEIVED(新建) | `cancel_order` QPMN 確認取消後;`create_order` 在建構時種入初始 `order_created` 條目並派發通知(from_status=NULL,郵件顯示 new)|
| [app/api/orders.py](../app/api/orders.py) | RECEIVED | 平台 republish 重置狀態 |
| [app/api/webhooks.py](../app/api/webhooks.py) | 各 QPMN 狀態 / PRODUCED | webhook 主流程與「全部組件生產完成」;以 `extra={"event_status": ...}` 保留原日誌欄位 |

非狀態變更的日誌（如 `order_oms_retry`、`barcode_generated`）直接調用 `OrderService.append_log`（tasks 層經 `_append_order_log` 薄包裝），不觸發通知；其條目同樣攜帶預設 `level: "info"`。

## 組態配置([app/core/config.py](../app/core/config.py))

```text
EMAIL_NOTIFY_RETRY_COUNT = 3          # 最大嘗試次數
EMAIL_NOTIFY_RETRY_COUNTDOWN = 60     # 指數退避基礎延遲(秒)
EMAIL_NOTIFY_RETRY_MAX_COUNTDOWN = 900  # 延遲上限(15 分鐘)
ADMIN_BASE_URL = ""                    # 管理後台基礎 URL;非空時郵件附「View Details」按鈕
```

重試視窗刻意短於 OMS/VFS postback(5 次/1 小時):遲到的通知郵件價值低,但 WARNING/ERROR 告警不能丟。郵件發送依賴既有的 `SENDGRID_API_KEY` / `SENDGRID_FROM_EMAIL` / `SENDGRID_FROM_NAME`。

## 測試覆蓋([tests/test_order_notifications.py](../tests/test_order_notifications.py))

| 測試類 | 驗證內容 |
|--------|----------|
| `TestStatusNotificationLevel` | 映射全覆蓋(新增 OrderStatus 未定級即失敗);cancelled=warning;failed/errored=error;正常流轉=info |
| `TestRecordStatusChange` | 設狀態+追加帶 level 條目;返回(舊狀態, 級別);`extra` 欄位合併(webhook 的 event_status);未傳 worker 不出現該鍵;logs=None 初始化 |
| `TestResolveNotificationRecipients` | 配置缺失/空/停用/空郵箱 → 無收件人;啟用級別只取自身郵箱 |
| `TestRenderOrderStatusEmail` | 主題格式;HTML escape(script 注入防護);純文字欄位 |
| `TestNotificationConfigSchema` | 預設全停用;非法郵箱 ValidationError;`model_dump` 與存儲格式一致 |

## 部署與運維注意事項

1. **先執行資料庫遷移**再部署代碼(`alembic upgrade head`);兩項變更均向後兼容,不停機安全。
2. 開發環境注意:FastAPI lifespan 的 `SQLModel.metadata.create_all` 會依新模型自動建 `notification_email_logs`,可能造成與 alembic 版本不一致(表已存在導致遷移失敗)。如遇到,先 `DROP TABLE notification_email_logs` 並 `ALTER TABLE clients DROP COLUMN notification_config`(僅開發庫),再跑遷移由其正式建表。
3. **API 與 worker 必須同步部署新鏡像**:worker 跑舊代碼時未註冊 `notify_order_status_email`,收到任務消息會當 unregistered 直接丟棄,`notification_email_logs` 行永遠卡在 `received` 且 `recipients` 為 NULL。新增 Celery 任務時必須同步在 `task_routes` 加路由條目(`@task(queue=...)` 裝飾器參數**不參與路由**)——缺路由時消息發往預設 `celery` 隊列,RabbitMQ 無此隊列且無 mandatory 標誌,消息被靜默丟棄。測試 `TestTaskRouting` 已鎖定全部 `tasks.*` 任務必須有顯式路由。
4. 通知任務複用 `order_notifying` 佇列,既有 worker 無需改動;但需重啟 worker 載入新任務代碼。
5. `notification_config` 修改即時生效:僅影響之後發生的狀態變更。
6. 排查發送問題:查 `notification_email_logs` 的 `process_status`/`details`/`retry_count`;`skipped` 的 `details` 會說明原因(級別未啟用 / 無收件人 / SendGrid 未配置)。若行長期停留 `received` 且 `recipients` 為 NULL,即為任務未被消費(路由缺失或 worker 舊代碼),可修復後重新派發 `notify_order_status_email.delay(log_id=...)`。
