# SiteFlow 訂單取消（Cancel Order）流程

## 概述

本文檔整理 order-gateway 目前實現的訂單取消全流程，包含：取消入口、流程節點、
哪些狀態允許取消、哪些狀態會被拒絕、QPMN 端取消 API 的互動細節，以及取消成功後
的後續影響（日誌、郵件通知、`source_order_id` 釋放等）。

核心原則：**先取消 QPMN 側，成功後才更新本地狀態**。本地訂單只有當 QPMN 返回
HTTP 200 且 `success=true` 時才會轉為 `CANCELLED`（從未推送 QPMN 的訂單除外）。

---

## 1. 取消入口總覽

系統共有 **3 個取消入口**：

| # | 入口 | 端點 | 觸發方 | 代碼位置 |
|---|------|------|--------|----------|
| 1 | 直接取消 API | `PUT /api/order/{source_account}/{source_order_id}/cancel` | 客戶端（OMS/VFS 等） | [`app/api/orders.py`](../app/api/orders.py) `cancel_order()` |
| 2 | 內容更新（取消 + 重建） | `PUT /api/order/{order_id}`（`orderData.shipments` 為空或缺失時） | 客戶端修改訂單內容/稿件 | [`app/api/orders.py`](../app/api/orders.py) content update path |
| 3 | QPMN 入站 webhook | `POST /api/webhook/qpmn/order-status`（header `x-qpmn-event-type: order_item_canceled`） | QPMN 主動取消 | [`app/api/webhooks.py`](../app/api/webhooks.py) `receive_order_status()` |

入口 1、2 共用同一核心服務方法 [`OrderService.cancel_order()`](../app/services/order.py)；
入口 3 是 QPMN 側發起的取消，走 webhook 狀態機路徑。

---

## 2. 直接取消 API 完整流程

### 2.1 流程圖

```text
客戶端 PUT /api/order/{source_account}/{source_order_id}/cancel
    │
    ▼
┌──────────────────────────────┐
│ ① OneFlow HMAC 認證          │  get_client_store_id()
│    X-Oneflow-* 簽名頭驗證    │  返回 client 的 store_id（bootstrap 憑證為 None）
└──────────┬───────────────────┘
           │ 401（認證失敗，由認證層直接返回）
           ▼
┌──────────────────────────────┐
│ ② 查找訂單                   │  source_account + source_order_id
│    按 store_id 做多租戶隔離  │
└──────────┬───────────────────┘
           │ 找不到 → 404 NotFound
           ▼
┌──────────────────────────────┐
│ ③ 本地狀態檢查               │  status ∈ NON_CANCELLABLE_STATUSES？
│    （見 §3 狀態對照表）      │
└──────────┬───────────────────┘
           │ 不可取消 → 寫 cancel_rejected 日誌 → 409 Conflict
           ▼
┌──────────────────────────────────────────────┐
│ ④ 呼叫 QPMN 取消 API                         │
│    cancel_qpmn_order()                       │
│    ├─ 無 store_order_id（從未推送 QPMN）     │
│    │    → 跳過 QPMN 呼叫，視為成功           │
│    ├─ 無 store_key → 失敗（Sentry 告警）     │
│    └─ PUT {QPMN_OPEN_API_URL}/orders/        │
│         {store_order_id}/cancel              │
│         Authorization: Basic {store_key}     │
│         超時 30s                             │
└──────────┬───────────────────────────────────┘
           │
     ┌─────┴──────────────────────┐
     │ QPMN 成功                  │ QPMN 失敗
     │ (HTTP 200 + success=true)  │ (逾時/非 200/success=false/store_key 缺失)
     ▼                            ▼
┌──────────────────────┐   ┌──────────────────────────────┐
│ ⑤ 本地狀態轉 CANCELLED│   │ 寫 qpmn_cancel_failed 日誌   │
│    record_status_change│   │ （含 HTTP 狀態碼與錯誤內容） │
│    + order_cancelled  │   │ → 502 BadGateway             │
│    日誌（WARNING 級） │   │ （本地狀態不變）             │
├──────────────────────┤   └──────────────────────────────┘
│ ⑥ 派發 WARNING 級    │
│    狀態變更郵件       │
├──────────────────────┤
│ ⑦ 返回成功響應        │
│    {"success": true,  │
│     "order_id": ...}  │
└──────────────────────┘
```

### 2.2 節點詳解

| 節點 | 行為 | 失敗結果 |
|------|------|----------|
| ① 認證 | 驗證 `X-Oneflow-Authorization` 等 HMAC 簽名頭（含 300 秒防重放窗口）；按 token 解析 client 的 `store_id` 用於訂單隔離 | 401 |
| ② 查找訂單 | 以 `source_account + source_order_id` 查詢，若認證方有 `store_id` 則附加過濾（多租戶隔離，看不到別的店鋪訂單） | 404 NotFound |
| ③ 狀態預檢 | 訂單狀態在 `NON_CANCELLABLE_STATUSES` 中則拒絕，寫入 `cancel_rejected` 日誌後拋 `OrderNotCancellableError` | 409 Conflict |
| ④ QPMN 取消 | 見 §5 QPMN 端行為。從未推送 QPMN（無 `store_order_id`）的訂單直接跳過外部呼叫，本地照常取消 | — |
| ⑤ 狀態落庫 | `record_status_change()` 將狀態設為 `CANCELLED` 並寫入帶 `WARNING` 級別的 `order_cancelled` 日誌 | — |
| ⑥ 郵件通知 | `enqueue_status_change_email()` 派發 WARNING 級通知（CANCELLED 不在 `EMAIL_MUTED_STATUSES` 靜音集），收件人取決於該 client 的 `notification_config.warning` 配置；未配置則記錄 `skipped` | — |
| ⑦ 成功響應 | 返回 `CancelledOrderResponse`：`{"success": true, "message": "Order cancelled successfully", "order_id": <內部訂單ID>}` | — |

> **注意**：直接取消路徑（本入口）**不會**主動通知 OMS / VFS（無 `notify_oms` / `notify_vfs`），
> 僅發送內部郵件通知。對外狀態通知只發生在 webhook 入口（見 §6）。
> 若 QPMN 在 API 取消後回推 `order_item_canceled` 事件，因訂單已處於終端狀態
> `CANCELLED`（`CANCELLED → CANCELLED` 不是合法轉移），webhook 會以
> `Invalid transition` 為由 SKIPPED，不會產生重複的對外通知（會觸發一條
> `INVALID_TRANSITION` Sentry 告警記錄）。

---

## 3. 可取消 / 拒絕狀態對照表

### 3.1 允許取消的狀態（7 個）

| 內部狀態 | 對外狀態 | 說明 |
|----------|----------|------|
| `RECEIVED` | `received` | 訂單剛接收（尚未推送 QPMN，取消時跳過 QPMN 呼叫） |
| `PENDING` | `received` | 待處理隊列中 |
| `VALIDATED` | `dataready` | 驗證通過 |
| `COOLING_OFF` | `dataready` | 冷靜期內（買家反悔窗口，正是冷靜期機制的設計目的） |
| `PROCESSING` | `dataready` | 處理中（檔案準備、排版等，尚未推送 QPMN） |
| `FAILED` | `error` | 推送/處理失敗，放棄重試而取消 |
| `ERRORED` | `error` | 異常狀態，人工決定取消 |

> 與狀態機 `ORDER_STATE_TRANSITIONS` 一致：`CANCELLED` 恰好只從上述 7 個狀態可達。

### 3.2 拒絕取消的狀態（5 個，返回 409 Conflict）

| 內部狀態 | 對外狀態 | 拒絕原因 |
|----------|----------|----------|
| `PRINTREADY` | `printready` | 已推送 QPMN 並通過審核，進入生產流程 |
| `PRINTED` | `printed` | 部分組件已列印完成 |
| `PRODUCED` | `produced` | 全部組件生產完成，準備出貨 |
| `SHIPPED` | `shipped` | 已出貨（終端狀態） |
| `CANCELLED` | `cancelled` | 已是取消終端狀態（冪等重複請求也返回 409） |

拒絕時錯誤訊息：`"Cannot cancel order with status '<status>'."`，
同時會在 `order.logs` 寫入一條 `cancel_rejected` 日誌。

> `NON_CANCELLABLE_STATUSES` 邊界與 QPMN 對齊：QPMN 側「已審核（reviewed）及之後」
> 即不可取消；本網關對應的 `PRINTREADY`（由 QPMN `order_item_audited` 事件驅動）起
> 即拒絕，因此正常時序下不會出現「本網關放行、QPMN 拒絕（422 ORDER_ITEM_REVIEWED）」
> 的情況——僅在兩側狀態存在短暫競態窗口時可能發生。

---

## 4. 錯誤響應格式（SiteFlow 兼容）

所有錯誤遵循 HP SiteFlow 格式（由 `_siteflow_error()` 構造，
經 `app/main.py` 的 exception handler 原樣輸出）：

```json
{
  "success": false,
  "error": {
    "message": "Cannot cancel order with status 'printready'.",
    "name": "Conflict",
    "code": 409
  }
}
```

| HTTP Code | Error Name | 場景 |
|-----------|------------|------|
| 401 | Unauthorized | OneFlow HMAC 認證失敗 |
| 404 | NotFound | 訂單不存在或不屬於該 store（訪問被拒） |
| 409 | Conflict | 訂單狀態不允許取消（見 §3.2） |
| 502 | BadGateway | QPMN 取消 API 呼叫失敗（詳見 §5.2） |
| 500 | InternalServerError | 未預期的內部錯誤（自動 rollback 後返回） |

成功響應：

```json
{
  "success": true,
  "message": "Order cancelled successfully",
  "order_id": "ord_xxxxxxxx"
}
```

---

## 5. QPMN 端取消 API 行為

### 5.1 請求細節

| 項目 | 內容 |
|------|------|
| URL | `PUT {QPMN_OPEN_API_URL}/orders/{store_order_id}/cancel`（即 QPMN 文檔 §3.3 `/open-api/v1/orders/{orderId}/cancel`） |
| 認證 | `Authorization: Basic {store_key}`（store_key 按訂單 `store_id` 從 client 配置查詢） |
| 超時 | 30 秒 |
| 冪等 | QPMN 聲明冪等 |
| QPMN 側規則 | 訂單下**所有**訂單項均未處於「reviewed（已審核）及之後」的狀態才允許取消，否則返回 422 `ORDER_ITEM_REVIEWED` |

### 5.2 失敗場景與 Sentry 告警

`cancel_qpmn_order()` 對每類失敗都有對應的 Sentry 整合告警
（`ALERTS["cancel"]`，`app/core/sentry_alerts.py`）：

| 場景 | 判定 | Sentry 告警鍵 | 級別 |
|------|------|---------------|------|
| 店鋪未配置 store_key | 查無 store_key | `NO_STORE_KEY` | warning |
| QPMN 逾時 | `httpx.TimeoutException` | `TIMEOUT` | error |
| 請求異常 | 其他網路/連線錯誤 | `REQUEST_FAILED` | error |
| QPMN 返回非 200 | 4xx/5xx | `REJECTED_HTTP` | error |
| HTTP 200 但 `success=false` | 業務拒絕 | `REJECTED_SUCCESS_FALSE` | error |

任何失敗都會：寫入 `qpmn_cancel_failed` 日誌（含 HTTP 狀態碼、錯誤內容、響應體）→
本地狀態**保持不變** → 網關向調用方返回 **502 BadGateway**（錯誤信息透傳 QPMN 原始錯誤）。

---

## 6. 入口 2：內容更新（取消 + 重建）

`PUT /api/order/{order_id}` 在 `orderData.shipments` 為空或缺失時走「內容更新」路徑，
本質是 **先取消舊訂單、再用相同 `source_order_id` 建立新訂單**：

1. 前置要求：請求必須同時攜帶 `destination` 與 `orderData`，否則 422
   （無法在部分欄位上重建訂單）。
2. 先對當前訂單執行 `cancel_order()`（完整流程同 §2）：
   - 不可取消 → 寫 `content_update_rejected` 日誌 → **409**
     `"Cannot update order content: order status '<status>' does not allow cancellation..."`
   - QPMN 取消失敗 → **502**。
3. 取消成功後以相同 `source_order_id`、原有訂單類型（`order_type`）建立新訂單
   （內部 `order_id` 不同）。
4. 響應攜帶 `cancelledOrderId`（被取消的舊訂單內部 ID）與新訂單完整資料。

> 依賴：`check_duplicate()` 會排除 `CANCELLED` / `ERRORED` 狀態的訂單，
> 因此同一個 `source_order_id` 在舊訂單取消後可立即重複提交。

---

## 7. 入口 3：QPMN 發起的取消（webhook）

QPMN 在店鋪訂單項被取消時推送 `order_item_canceled` 事件
（`POST /api/webhook/qpmn/order-status`，詳見 `docs/QPMN_API.md` §5）：

1. 驗證 `x-qpmn-hmac-sha256` 簽名，記錄入站 `WebhookLog`。
2. `EVENT_STATUS_MAP` 將 `order_item_canceled` 映射為內部狀態 `CANCELLED`。
3. 狀態機檢查 `can_transition()`：
   - 當前狀態屬於 §3.1 的 7 個可取消狀態 → 狀態更新為 `CANCELLED`
     （日誌事件 `qpmn_status_webhook`，攜帶 `event_status` 額外欄位）。
   - 已處於 `PRINTREADY`/`PRINTED`/`PRODUCED`/`SHIPPED`/`CANCELLED` 等狀態 →
     返回 `Invalid transition`，事件標記 SKIPPED，觸發 `INVALID_TRANSITION` Sentry 告警。
     （`order_item_canceled` 不適用「組件事件被超越」的靜默忽略規則——取消事件值得顯式暴露。）
4. 取消成功後**會**對外通知（與直接取消路徑不同）：
   - `notify_oms`：OMS API-002 推送 `status=cancelled`；
   - `notify_vfs`：向訂單 `order_data.postbackAddress` 推送
     `{"orderId": <source_order_id>, "status": "cancelled"}`；
   - WARNING 級狀態變更郵件。

---

## 8. 取消成功後的後續影響

| 影響項 | 說明 |
|--------|------|
| 訂單日誌 | 新增 `order_cancelled` 條目（`level=warning`，action 取決於入口：API 取消 / webhook） |
| 郵件通知 | WARNING 級（未靜音），發送給該 client `notification_config.warning` 中啟用的收件人；主題格式 `[WARNING] Order {source_order_id} Cancelled` |
| 狀態查詢 | 對外（SiteFlow 格式）呈現 `orderData.status = "cancelled"`（終端狀態） |
| `source_order_id` 釋放 | `CANCELLED` 訂單不參與重複檢測，同一 `source_order_id` 可重新提交新訂單 |
| 平台軟刪除 | `CANCELLED` 屬於 `DELETABLE_STATUSES`：未推送過 QPMN（無 `store_order_id`）的終端訂單可經平台 API 軟刪除（`order_deleted` 日誌） |
| 平行卡版本鏈 | 提交版本 N（N ≥ 2）要求前一版本已處於 `CANCELLED` 狀態，否則拒絕提交（`"still being processed"`） |

---

## 9. 相關代碼位置速查

| 內容 | 位置 |
|------|------|
| 取消 API 端點 | `app/api/orders.py` — `cancel_order()`（`PUT /order/{source_account}/{source_order_id}/cancel`） |
| SiteFlow 錯誤構造 | `app/api/orders.py` — `_siteflow_error()` |
| 內容更新（取消+重建） | `app/api/orders.py` — `PUT /order/{order_id}` content update path |
| 核心取消服務 | `app/services/order.py` — `OrderService.cancel_order()` |
| QPMN 取消呼叫 | `app/services/order.py` — `OrderService.cancel_qpmn_order()` |
| 不可取消狀態集合 | `app/services/order.py` — `NON_CANCELLABLE_STATUSES` |
| 可刪除/可重發狀態集合 | `app/services/order.py` — `DELETABLE_STATUSES` / `REPUBLISHABLE_STATUSES` |
| 狀態機（合法轉移） | `app/models/order.py` — `ORDER_STATE_TRANSITIONS` / `can_transition()` |
| 事件 → 狀態映射 | `app/models/order.py` — `EVENT_STATUS_MAP`（`order_item_canceled` → `CANCELLED`） |
| QPMN webhook 處理 | `app/api/webhooks.py` — `receive_order_status()` |
| 狀態變更記錄/郵件派發 | `app/services/order_notifications.py` — `record_status_change()` / `enqueue_status_change_email()` |
| 通知級別映射 | `app/models/notification.py` — `STATUS_NOTIFICATION_LEVEL`（`CANCELLED` → `warning`） |
| 取消相關 Sentry 告警 | `app/core/sentry_alerts.py` — `ALERTS["cancel"]` |
| QPMN 取消接口官方文檔 | `docs/QPMN_API.md` §3.3 |
| 狀態生命週期總覽 | `docs/SITEFLOW_ORDER_STATUS_LIFECYCLE.md` |
