# SiteFlow 訂單創建（Order Create）流程

## 概述

本文檔整理 order-gateway 目前實現的訂單創建全流程：從 `POST /api/order` 同步提交、
Celery 異步處理管線（publish → validate → push），到 QPMN 生產階段經 webhook
回推的狀態更新，直到訂單最終出貨。

整體分三個階段：

| 階段 | 執行環境 | 狀態範圍 | 說明 |
|------|----------|----------|------|
| ① 同步提交 | API 進程 | `RECEIVED` | 驗證 + 建單 + 返回響應 |
| ② 異步處理管線 | Celery worker（3 個隊列） | `PENDING → VALIDATED → [COOLING_OFF] → PROCESSING` | 檔案處理、OMS 地址抓取、推送 QPMN |
| ③ 生產狀態回推 | Webhook 端點 | `PRINTREADY → PRINTED → PRODUCED → SHIPPED` | QPMN 事件驅動，並對外通知 OMS/VFS |

---

## 1. 同步階段：POST /api/order 提交流程

端點：`POST /api/order`（[app/api/orders.py](../app/api/orders.py) `submit_order()`）

### 1.1 流程圖

```text
客戶端 POST /api/order
    │
    ▼
① OneFlow HMAC 認證（get_client_store_id）→ store_id
    │
    ▼
② 重複檢測（冪等）check_duplicate ──重複──→ 400 "Source Order ID already exists"
    │  （排除 CANCELLED/ERRORED/已軟刪除的訂單）
    ▼
③ 平行卡檢測 parse_parallel_card_id
    ├─ 格式符合且有母卡 ──→ 跳過 items 驗證；檢查版本鏈（N≥2 需前一版已 CANCELLED）
    │      ├─ 前一版不存在 ──→ 400 "was not submitted"
    │      └─ 前一版仍活躍 ──→ 400 "still being processed"
    ├─ 格式符合但無母卡 ──→ 400 "unknown base card order"
    ▼
④ items 驗證（非平行卡才執行）
    ├─ SKU 校驗：每個 item.sku 須能按 source_sku（精確/正則）在本 store 解析 ──失敗──→ 400 "Invalid or inactive SKU"
    └─ 文件可達性：components[].fetch=true 的 URL 須可達（HEAD / 流式 GET 探測）──失敗──→ 400 "File not accessible"
    │
    ▼
⑤ create_order：寫入 DB，初始狀態 RECEIVED，寫 order_created 日誌，
   派發 INFO 級建立通知郵件，投遞 publish_order Celery 任務
    │
    ▼
⑥ OSS 上傳訂單 payload（orders/{order_id}.json）→ 預簽名 URL（失敗僅記日誌，不影響建單）
    │
    ▼
⑦ 返回 SiteFlow 格式響應：{_id, url, timestamp, sourceAccountId}
```

### 1.2 節點詳解

| 節點 | 行為 | 失敗結果 |
|------|------|----------|
| ① 認證 | OneFlow HMAC 簽名頭驗證（300 秒防重放），按 token 取得 client 的 `store_id` 做多租戶隔離 | 401 |
| ② 重複檢測 | 以 `source_order_id` + `store_id` 查重；`CANCELLED`/`ERRORED`/軟刪除訂單不算重複（`source_order_id` 可複用） | 400（SiteFlow 格式，code 208） |
| ③ 平行卡檢測 | `sourceOrderId` 形如 `aaaaaaa[-_]b_Sccccc`（母卡 ID + 版本號 + `_S` 出貨號）視為平行卡；母卡查找按 store 隔離 | 見流程圖三種 400 |
| ④ items 驗證 | SKU 按 `source_sku` 精確或正則匹配（僅 active、僅本 store）；文件探測先 HEAD、403/405/501 時退回流式 GET，2xx/3xx 視為可達 | 400 `Validation Failed` |
| ⑤ 建單 | 生成 UUID `order_id`，狀態 `RECEIVED`，`version=1`，寫 `order_created` 日誌（INFO），派發建立通知郵件，投遞 `publish_order` 任務（`order_publishing` 隊列） | 500（rollback 後返回） |
| ⑥ OSS 上傳 | 訂單完整 payload 上傳 OSS 供下載審閱；失敗不阻斷（`url` 返回 null） | 不影響建單 |
| ⑦ 響應 | `{"_id": <order_id>, "url": <預簽名URL>, "timestamp": "...Z", "sourceAccountId": base64(store_id)}` | — |

> **驗證專用端點**：`POST /api/order/validate` 執行與 ③④ 相同的驗證規則但不建單，
> 供客戶端提交前預檢。

### 1.3 錯誤響應格式（SiteFlow 兼容）

```json
{
  "error": {
    "ofError": true,
    "statusCode": 400,
    "code": 208,
    "message": "Validation Failed",
    "validations": [
      { "path": "orderData.items.0.sku", "message": "Invalid or inactive SKU" }
    ],
    "mongoErr": true
  }
}
```

| HTTP Code | 場景 |
|-----------|------|
| 400 | 重複訂單 / SKU 無效 / 文件不可達 / 平行卡規則不符 |
| 422 | 請求體 schema 校驗失敗（Pydantic） |
| 500 | 內部錯誤（自動 rollback） |

---

## 2. 異步階段：Celery 處理管線

三個任務按順序鏈式執行（[app/tasks/orders.py](../app/tasks/orders.py)），
每步開頭都用 `can_transition()` 校驗狀態機，防止非法轉移（例如已取消的訂單
不會被管線繼續推進）。

### 2.1 管線總覽

```text
publish_order（隊列 order_publishing）
    RECEIVED → PENDING：下載設計檔 → PDF 拆頁 →（可選）PDF 轉 PNG →
    （平行卡）加浮水印 → 上傳 QPMN 檔案空間 → order.files 落庫
    失敗任何一步 → FAILED（order_processing_failed 日誌）
        │
        ▼ 鏈式派發
validate_order（隊列 order_validating）
    PENDING → VALIDATED：向 OMS 抓取收貨/帳單地址（orderNo = source_order_id）
    ├─ OMS 503/逾時 → 指數退避重試（5 次，基礎 300s，上限 3600s），耗盡 → FAILED
    ├─ 無收貨地址 → FAILED
    ├─ VALIDATED 後立即通知 OMS/VFS dataready（QPMN 不會發此事件，需自行通知）
    └─ 冷靜期：client 配置 cooling_off_seconds > 0 時 → COOLING_OFF，
       push_order 以 countdown 延遲排程；否則立即派發
        │
        ▼ 鏈式派發（可帶 countdown）
push_order（隊列 order_pushing）
    → PROCESSING：構建 QPMN payload（含地址/SKU/檔案/平行卡條碼）
    POST 到 QPMN 建單（Open API /orders 或 legacy /store/orders，Basic store_key）
    ├─ success=true → PROCESSING，記錄 store_order_id 與 store_order_item_ids（TI-65）
    ├─ success=false（業務拒絕）→ FAILED（不重試，Sentry REJECTED）
    ├─ 503/504 → 指數退避重試（5 次，基礎 900s，上限 7200s），耗盡 → FAILED
    └─ 逾時 → 指數退避重試（5 次，基礎 900s，上限 7200s），耗盡 → FAILED
```

### 2.2 publish_order：檔案處理（RECEIVED → PENDING）

1. 狀態轉 `PENDING`（`order_publishing_started` 日誌；郵件靜音——PENDING 屬
   `EMAIL_MUTED_STATUSES`）。
2. 逐 item 逐 component 處理設計檔：
   - 下載檔案到臨時目錄；失敗 → FAILED（"Cannot download design files"）。
   - PDF → `split_pdf` 拆成單頁；失敗 → FAILED。jpg/jpeg/png 原樣通過；
     其他格式 → FAILED（僅支持 pdf/jpg/png）。
   - `CONVERT_TO_PNG=true` 時（預設關閉）：單頁 PDF 以 `PDF_TO_PNG_DPI`（300）
     轉 PNG，寫 `design_files_converted_to_png` 日誌。
   - 平行卡訂單：每個檔案加 "Topps Now 客供產品" 浮水印並轉 PNG
     （`parallel_card_watermarked` 日誌）。
   - `upload_to_qpmn` 上傳至 QPMN 檔案空間（以 store_key 認證）；失敗 → FAILED。
   - SKU 解析：`items[].sku` 可為內部 ID、字面 `source_sku` 或正則模式，
     統一解析為內部 `sku_id` 後作為 `order.files` 的鍵（按 store 隔離匹配）。
3. `order.files` 落庫後鏈式派發 `validate_order`。

### 2.3 validate_order：地址抓取與冷靜期（PENDING → VALIDATED [→ COOLING_OFF]）

1. 寫 `order_validating_started` 日誌。
2. 地址抓取（OMS HUB4 API，`orderNo` = `source_order_id`，內部 UUID 對 OMS 不可見）：
   - 平行卡：複用母卡已存地址；缺帳單地址或無母卡地址時以母卡前綴向 OMS 補抓
     （OMS 只認得母卡 orderNo）。地址行統一重鍵到本訂單。
   - 普通訂單：直接以 `source_order_id` 抓取 delivery/billing。
   - `OMSRetryableError`（503/逾時）：指數退避重試，配置
     `OMS_VALIDATE_RETRY_COUNT=5`、基礎 300s、上限 3600s（1 小時）；
     每次重試寫 `order_oms_retry` 日誌；耗盡 → FAILED。
   - OMS 未返回收貨地址 → FAILED（"No delivery address returned by OMS"）。
3. 狀態轉 `VALIDATED`（`order_validated` 日誌 + INFO 郵件）。
4. **自行通知 OMS/VFS `dataready`**：QPMN 確認不會為 VALIDATED 發任何
   `order_item_*` 事件，因此由本任務直接建立 OMS/VFS 出站日誌並派發
   `notify_oms` / `notify_vfs`（詳見 §4.2）。
5. 冷靜期（COOLING_OFF，詳見 `docs/ORDER_COOLING_OFF_STATUS.md`）：
   - `client.cooling_off_seconds > 0` 時：狀態轉 `COOLING_OFF`，秒數快照到
     `order.cooling_off_seconds`，寫 `order_cooling_off` 日誌並發 INFO 郵件
     （郵件正文附冷靜期時長）；`push_order.apply_async(countdown=秒數)`。
   - 未配置：立即派發 `push_order`。
   - COOLING_OFF 為內部專用狀態，不通知 OMS/VFS（對外仍是 dataready），
     冷靜期內可取消訂單（見 `docs/SITEFLOW_ORDER_CANCEL_FLOW.md`）。

### 2.4 push_order：推送 QPMN 建單（→ PROCESSING）

1. 寫 `order_pushing_started` 日誌；狀態機要求當前為 `VALIDATED` 或 `COOLING_OFF`。
2. 平行卡條碼：取母卡 `store_order_id` 作為 `barcode` 持久化（`barcode_generated`
   日誌），payload 中每個 item 的 `supplierStockNo` = 條碼 + 兩位序號（01、02…）；
   母卡尚未推送時省略該欄位。
3. `build_push_payload()` 構建完整 payload 並持久化到 `order.creation_payload`
   （每次重試覆蓋，供審計與重放）。
4. 呼叫 QPMN 建單 API：
   - Open API（預設，`QPMN_ORDER_API_VERSION="open"`）：
     `POST {QPMN_OPEN_API_URL}/orders`
   - Legacy：`POST {QPMN_API_URL}/store/orders`
   - `Authorization: Basic {store_key}`，超時 30s。
5. 結果處理：
   - `success=true`：狀態轉 `PROCESSING`（`order_push_success` 日誌；郵件靜音——
     PROCESSING 屬靜音集）。記錄 `store_order_id`（取 `data.id` / `data.externalId`
     / `data.orderId`，新舊 API 兼容）與 `store_order_item_ids`（TI-65：記錄 QPMN
     分配的每個 item id，供後續判斷「全部組件生產完成」）。
   - `success=false`（業務拒絕，如圖片比例不合）：狀態轉 `FAILED`
     （`order_push_failed` 日誌 + ERROR 郵件 + Sentry `REJECTED`），**不重試**。
   - 503/504（服務暫時不可用 / 閘道逾時）：指數退避重試，配置同逾時分支
     （`QPMN_PUSH_RETRY_COUNT=5`、基礎 900s、上限 7200s），每次重試寫
     `order_push_retry` 日誌（首次觸發 Sentry `RETRY_503`/`RETRY_504` 告警）；
     重試耗盡 → FAILED（`RETRY_EXHAUSTED_503`/`RETRY_EXHAUSTED_504` 告警）。
   - 逾時（`httpx.TimeoutException`）：指數退避重試，配置
     `QPMN_PUSH_RETRY_COUNT=5`、基礎 900s、上限 7200s（2 小時），
     每次寫 `order_push_timeout` 日誌；耗盡 → FAILED。
   - 未預期異常：FAILED + Sentry（tag `failure_type=unexpected_exception`）。

> `_mark_order_failed()` 是管線統一的失敗出口：狀態轉 `FAILED`
> （`order_processing_failed` 日誌，ERROR 級）並派發 ERROR 郵件。
> FAILED 訂單可經平台 API republish（重跑管線）或取消/刪除。

---

## 3. Webhook 狀態更新（QPMN → 網關）

端點：`POST /api/webhook/qpmn/order-status`（[app/api/webhooks.py](../app/api/webhooks.py)
`receive_order_status()`），承載訂單推送 QPMN 之後的全部狀態推進。

### 3.1 處理流程

```text
QPMN POST /api/webhook/qpmn/order-status
    headers: x-qpmn-event-type / x-qpmn-event-id / x-qpmn-hmac-sha256
    │
    ▼
① 寫入入站 WebhookLog（原始 payload + headers，無論後續成敗）
    │
    ▼
② HMAC 簽名驗證：hex(HMAC-SHA256(key=store_key, message=原始請求體))
   逐一比對所有 active client 的 store_key ──失敗──→ 401（日誌記 FAILED）
    │
    ▼
③ 冪等去重（x-qpmn-event-id）：同一事件重複投遞 → 200 確認但不重複處理（SKIPPED）
    │
    ▼
④ 事件類型解析：EVENT_STATUS_MAP 映射未知類型 → SKIPPED（軟失敗 + 告警）
    │
    ▼
⑤ 按 body.orderId（= store_order_id）查找訂單 ──找不到──→ FAILED
    │
    ▼
⑥ 狀態機校驗 can_transition(當前狀態 → 事件狀態)
    ├─ 合法 → 狀態更新（qpmn_status_webhook 日誌）+ 級別郵件
    ├─ 非法但屬「過時組件事件」（見 §3.3）→ SKIPPED（200 確認）
    │    └─ order_item_produced 落後時：仍通知 OMS 單組件 printed，
    │       並在全部組件完成時推進 PRODUCED（TI-65）
    └─ 非法 → SKIPPED（success=false "Invalid transition" + 告警）
    │
    ▼
⑦ package_shipped 事件：另持久化 OrderShipment（物流單號/URL/承運商/日期）
    │
    ▼
⑧ 建立出站日誌並派發 notify_oms / notify_vfs（見 §4.2）
```

### 3.2 事件類型與狀態映射

| x-qpmn-event-type | 內部狀態 | 觸發時機（QPMN 側） | OMS 狀態詞彙 |
|-------------------|----------|---------------------|--------------|
| `order_item_received` | `RECEIVED` | 訂單項已確認待審核 | `received` |
| `order_item_audited` | `PRINTREADY` | 訂單項審核通過 | `printready` |
| `order_item_produced` | `PRINTED` | 訂單項生產完成 | `printed` |
| `package_shipped` | `SHIPPED` | 包裹發貨 | `shipped` |
| `order_item_canceled` | `CANCELLED` | 訂單項被取消 | `cancelled` |
| `order_item_failed` | `ERRORED` | 訂單項不可生產/失敗 | `error` |

> QPMN 每個訂單項各發一條事件；多組件訂單的事件會交錯到達，
> 只有最超前組件的狀態會落在訂單上（`ITEM_EVENT_STATUS_ORDER` 線性比較）。
> 聚合狀態 `PRODUCED` 不由 QPMN 事件直接驅動，而由網關按
> `store_order_item_ids` 全部完成判斷（TI-65，僅通知 OMS）。

### 3.3 過時事件（superseded）處理規則

| 情境 | 處理 |
|------|------|
| 訂單已 `PROCESSING`，收到 `order_item_received` | 視為過時確認，靜默 SKIPPED（推送流程早已同步走完 RECEIVED/PENDING/VALIDATED） |
| 訂單已 `PRODUCED`/`CANCELLED`/`SHIPPED`/`ERRORED`，收到 `order_item_received/audited/produced` | 落後組件事件，確認但狀態不回退 |
| 落後的 `order_item_produced` | 仍通知 OMS 該組件 printed；若恰好補齊全部組件，推進 `PRODUCED` |
| `order_item_canceled` / `order_item_failed` 非法轉移 | **不適用**靜默規則，走 Invalid transition 暴露 + 告警 |

---

## 4. 對外通知（OMS / VFS / 郵件）

### 4.1 各狀態的對外通知矩陣

| 內部狀態 | 通知 OMS | 通知 VFS | 郵件（級別） | 備註 |
|----------|:--------:|:--------:|--------------|------|
| `RECEIVED` | — | — | 建立（info） | API 同步階段 |
| `PENDING` | — | — | 靜音 | 內部狀態 |
| `VALIDATED` | dataready | dataready | info | **網關自行通知**（QPMN 不發此事件） |
| `COOLING_OFF` | — | — | info（附時長） | 內部狀態，對外仍 dataready |
| `PROCESSING` | — | — | 靜音 | 內部狀態 |
| `PRINTREADY` | printready | printready | info | webhook 驅動 |
| `PRINTED`（首組件） | printed | printed | info | webhook 驅動 |
| `PRINTED`（後續組件） | printed | printed | —（狀態未變） | TI-65 單組件通知 |
| `PRODUCED` | produced | — | info | 聚合狀態，僅通知 OMS |
| `SHIPPED` | shipped | shipped（含物流資訊） | info | package_shipped 驅動 |
| `CANCELLED` | cancelled* | cancelled* | warning | *僅 webhook 入口的取消會通知（直接取消 API 不通知 OMS/VFS，見取消流程文檔） |
| `FAILED` / `ERRORED` | —（FAILED）/ error（ERRORED，事件驅動） | — | error | FAILED 為內部處理失敗，無 QPMN 事件 |

### 4.2 notify_oms / notify_vfs 任務（隊列 order_notifying）

- **OMS**：HUB4 加密運輸（API-002 `POST /api/order/status`），payload 含
  `orderNo`（內部 order_id）、狀態、shipments。
- **VFS**：SiteFlow 風格 postback，URL 取自訂單 `order_data.postbackAddress`
  （未設置則跳過），payload `{"orderId": source_order_id, "status": ...}`；
  SHIPPED 事件另附物流明細。
- 重試策略：5xx/網路錯誤指數退避重試（5 次，基礎 300s，上限 3600s）；
  4xx/業務錯誤標記 failed 不重試。每次嘗試的狀態記錄在 `webhook_logs.process_status`。
- 郵件通知按級別路由到 client 的 `notification_config`
  （詳見 `docs/ORDER_STATUS_NOTIFICATION_SYSTEM.md`）。

---

## 5. 端到端狀態時間線

```text
[同步]  POST /api/order ──→ RECEIVED（建單 + 建立郵件）
            │ 派發 publish_order
[異步]  PENDING（檔案下載/拆頁/轉檔/浮水印/上傳 QPMN）
            │ 派發 validate_order
        VALIDATED（OMS 地址抓取成功；通知 OMS/VFS dataready）
            │ 有冷靜期配置？
            ├─ 是 → COOLING_OFF（countdown 等待，可取消）
            └─ 否 ↓ 派發 push_order
        PROCESSING（QPMN 建單成功，記錄 store_order_id / item_ids）
            │
[webhook] order_item_audited ──→ PRINTREADY（此後不可取消）
            │
        order_item_produced ──→ PRINTED（首組件；後續組件單獨通知 OMS）
            │ 全部 store_order_item_ids 均回報 produced（TI-65）
        PRODUCED（聚合；僅通知 OMS）
            │
        package_shipped ──→ SHIPPED（終端；持久化物流記錄，通知 OMS/VFS）
```

失敗分支：管線任何步驟失敗 → `FAILED`（ERROR 郵件，可 republish 重跑或取消/刪除）；
QPMN 生產失敗（`order_item_failed`）→ `ERRORED`（需人工介入後重試或取消）。

---

## 6. 相關配置速查（app/core/config.py）

| 配置 | 預設 | 說明 |
|------|------|------|
| `QPMN_ORDER_API_VERSION` | `open` | 建單 API 版本：`open`（/orders）或 `legacy`（/store/orders） |
| `QPMN_PUSH_RETRY_COUNT` | 5 | QPMN 建單逾時重試次數 |
| `QPMN_PUSH_RETRY_COUNTDOWN` | 900 | 重試基礎延遲（秒） |
| `QPMN_PUSH_RETRY_MAX_COUNTDOWN` | 7200 | 重試延遲上限（2 小時） |
| `OMS_VALIDATE_RETRY_COUNT` | 5 | OMS 地址抓取重試次數（基礎 300s / 上限 3600s） |
| `OMS_NOTIFY_RETRY_COUNT` | 5 | OMS 通知重試次數（基礎 300s / 上限 3600s） |
| `VFS_NOTIFY_RETRY_COUNT` | 5 | VFS postback 重試次數（基礎 300s / 上限 3600s） |
| `CONVERT_TO_PNG` | false | 設計 PDF 頁面是否轉 PNG 後上傳 |
| `PDF_TO_PNG_DPI` | 300 | PDF 轉 PNG 解析度 |

Celery 隊列（app/core/rabbitmq.py）：`order_publishing` → `order_validating` →
`order_pushing` → `order_notifying`（任務必須顯式配置 `task_routes` 路由，
缺路由時消息會被 RabbitMQ 靜默丟棄）。

---

## 7. 相關代碼位置速查

| 內容 | 位置 |
|------|------|
| 提交訂單端點 | `app/api/orders.py` — `submit_order()`（`POST /order`） |
| 驗證專用端點 | `app/api/orders.py` — `validate_order()`（`POST /order/validate`） |
| 文件可達性探測 | `app/api/orders.py` — `is_file_accessible()` |
| 平行卡 ID 解析 | `app/services/order.py` — `parse_parallel_card_id()` |
| 建單服務（含任務派發） | `app/services/order.py` — `OrderService.create_order()` |
| QPMN 推送 payload 構建 | `app/services/order.py` — `build_push_payload()` |
| 檔案處理任務 | `app/tasks/orders.py` — `publish_order()` |
| 地址抓取/冷靜期任務 | `app/tasks/orders.py` — `validate_order()` |
| QPMN 建單任務 | `app/tasks/orders.py` — `push_order()` |
| 失敗統一出口 | `app/tasks/orders.py` — `_mark_order_failed()` |
| Webhook 狀態更新 | `app/api/webhooks.py` — `receive_order_status()` |
| 簽名驗證 | `app/api/webhooks.py` — `_verify_qpmn_signature()` |
| TI-65 全組件完成判定 | `app/api/webhooks.py` — `_record_produced_item_and_maybe_complete()` |
| 事件/狀態映射與狀態機 | `app/models/order.py` — `EVENT_STATUS_MAP` / `ORDER_STATE_TRANSITIONS` |
| 對外通知任務 | `app/tasks/notifications.py` — `notify_oms()` / `notify_vfs()` / `notify_order_status_email()` |
| 取消流程（銜接） | `docs/SITEFLOW_ORDER_CANCEL_FLOW.md` |
| 冷靜期機制 | `docs/ORDER_COOLING_OFF_STATUS.md` |
| 狀態生命週期總覽 | `docs/SITEFLOW_ORDER_STATUS_LIFECYCLE.md` |
| QPMN 官方接口文檔 | `docs/QPMN_API.md` |
