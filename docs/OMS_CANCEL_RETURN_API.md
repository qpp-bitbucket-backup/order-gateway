# OMS 取消 / 退貨 API

供 OMS 系統調用的兩個對外接口：透過 `sourceOrderId` 取消訂單、對已出貨訂單發起退貨。

- 實現：`app/api/oms.py`（路由前綴 `/api/oms`）
- 授權：`app/core/auth_oms.py`（與 VFS 使用的 OneFlow 簽名頭機制相同，但使用 **per-client 的** `oms_token` / `oms_secret` 憑證 —— 存於 `clients` 表，經 platform API 管理，詳見 1.5）
- 交互式文檔：ReDoc 頁面 `GET /oms-api-doc`（單一 Order 分組，條目 POST Cancel / POST Return，schema 亦單獨暴露於 `GET /openapi_oms.json`）

---

## 1. 授權（Authentication）

與 VFS 調用 `POST /api/order` 的簽名機制完全一致 —— OMS 端的簽名代碼可與 VFS 端同構，僅需替換為目標 client 的 `oms_token` / `oms_secret`。

### 1.1 Required Headers

| Header | 說明 |
| --- | --- |
| `x-oneflow-authorization` | `{oms_token}:{signature}`，冒號分隔（oms_token 為 clients 表中該 client 的 OMS 憑證）|
| `x-oneflow-date` | 簽名所用的 ISO 8601 時間戳（如 `2026-09-23T10:00:00.000Z`），有效期 ±300 秒 |
| `x-oneflow-algorithm` | 固定 `SHA256` |

### 1.2 簽名算法

對以下字符串計算 **HMAC-SHA256**（hex 編碼），密鑰為該 client 的 `oms_secret`：

```
{HTTP_METHOD} {PATH} {TIMESTAMP}
```

示例：

```
POST /api/oms/order/ORD-123/cancel 2026-09-23T10:00:00.000Z
POST /api/oms/order/ORD-123/return 2026-09-23T10:00:00.000Z
```

注意：PATH 含路徑參數的實際值（如 `ORD-123`），且不同 METHOD / PATH / TIMESTAMP 需分別重新簽名。

### 1.3 Python 簽名示例

```python
import hashlib
import hmac
from datetime import datetime, timezone

def build_headers(api_key: str, api_secret: str, method: str, path: str) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    string_to_sign = f"{method.upper()} {path} {ts}"
    signature = hmac.new(
        api_secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {
        "x-oneflow-authorization": f"{api_key}:{signature}",
        "x-oneflow-date": ts,
        "x-oneflow-algorithm": "SHA256",
        "Content-Type": "application/json",
    }
```

### 1.4 授權失敗響應

| HTTP | X-Error-Code | 場景 |
| --- | --- | --- |
| 401 | `MISSING_AUTHORIZATION` / `MISSING_DATE` / `MISSING_ALGORITHM` | 缺少對應 Header |
| 401 | `UNSUPPORTED_ALGORITHM` | algorithm 不是 SHA256 |
| 401 | `INVALID_AUTHORIZATION_FORMAT` | authorization 不含冒號或 token/簽名為空 |
| 401 | `EXPIRED_TIMESTAMP` | 時間戳超出 ±300 秒窗口 |
| 403 | `INVALID_TOKEN` | `oms_token` 不匹配任何已配置 OMS 憑證的 active client（含 client 未啟用 / oms_secret 未配置 / client 已停用）|
| 403 | `INVALID_SIGNATURE` | 簽名驗證失敗 |

### 1.5 憑證管理（platform API）

憑證為 **per-client** 存儲：`clients` 表的 `oms_token` / `oms_secret` 欄位（NULL = 該 client 未啟用 OMS 授權），經 platform API（JWT admin）管理，不使用環境變量：

| 端點 | 說明 |
| --- | --- |
| `POST /api/platform/clients/oms_secret` | 查詢（reveal）憑證 —— body `{"store_id": "...", "password": "..."}`，需重新確認登錄密碼後才返回 `oms_token` / `oms_secret`（未配置則返回 null）|
| `PUT /api/platform/clients/oms_secret` | 更新憑證 —— 顯式提供 `oms_token` 和/或 `oms_secret`（secret ≥ 32 字符），或 `rotate=true` 由網關生成新配對（12 字符 hex token + 32 字符 hex secret）；顯式值與 rotate 同時提供、或均不提供 → 422；僅更新 token 時響應不回顯未變更的 secret |

注意：
- `oms_token` 全局唯一（一個 token 僅解析到一個 client）；與該 client 的 OneFlow `token` / `secret` 完全獨立，不可互用
- **新建 client 時自動生成** OMS 憑證配對（兩個創建端點均如此），`oms_secret` 僅在創建響應中一次性返回；上述 PUT 端點用於後續輪換（rotate）或顯式更新
- 未配置 OMS 憑證（NULL）的存量 client 無法通過 OMS 端點認證（403 `INVALID_TOKEN`）

---

## 2. 取消訂單

### `POST /api/oms/order/{source_order_id}/cancel`

與 SiteFlow 取消端點（`PUT /api/order/{source_account}/{source_order_id}/cancel`）的區別：**無需 source_account**，僅憑 sourceOrderId 查單。

#### Request Body（可選）

```json
{ "reason": "customer request" }
```

#### 查單規則

`source_order_id` 並非全局唯一（跨店可能撞號；改圖流程取消後會重用同一 ID）：

- 恰有 1 筆 active 訂單 → 執行取消
- 0 筆 → 記錄延遲取消並返回 **200**（見下方「延遲取消」）
- 多筆 → 409 Conflict，響應附候選列表 `candidates`（order_id / store_id / status），需人工核實

#### 處理邏輯

1. 已是 `cancelled` → **冪等返回 200**（`message: "Order already cancelled"`，OMS 重試安全）
2. `shipped` → 409 Conflict（已進入生產/出貨流程，不可取消）
3. 其他狀態 → 調用 QPMN cancel API，成功後訂單轉 `cancelled` 併發送狀態變更通知郵件；QPMN 失敗 → 502 BadGateway

#### 延遲取消（order_cancel_list）

OMS 的取消請求可能先於 VFS 的建單請求到達（顧客在 OMS 下單後立即反悔，而 VFS 的訂單創建請求尚未抵達）：

1. cancel API 返回 **200**（表示延遲取消已受理），同時將該 `sourceOrderId` 寫入 `order_cancel_list` 表為 `pending` 記錄（冪等：重複的延遲取消請求僅更新 `reason`，不產生重複行）
2. 之後 VFS 提交的建單請求進入 `order_publishing` worker 時，若存在同 `sourceOrderId` 的 `pending` 記錄 → **立即本地取消**：訂單狀態直接轉 `cancelled`（不調用 QPMN cancel API —— 訂單此時尚未推送 QPMN），發送狀態變更通知郵件；記錄標記為 `processed` 並回填內部 `order_id` 與 `processed_message`
3. 已 `processed` 的記錄不會再次觸發取消，訂單照常走發布流程

#### 成功響應

取消成功：

```json
{
  "success": true,
  "message": "Order cancelled successfully",
  "order_id": "6f8e...",
  "source_order_id": "ORD-123",
  "status": "cancelled"
}
```

訂單尚未到達（延遲取消已受理，`order_id` / `status` 為 null）：

```json
{
  "success": true,
  "message": "Order not found yet; cancellation recorded and will be applied when the order arrives",
  "order_id": null,
  "source_order_id": "ORD-123",
  "status": null
}
```

#### 錯誤響應（SiteFlow 格式）

```json
{
  "success": false,
  "error": { "message": "...", "name": "Conflict", "code": 409 }
}
```

| HTTP | Error Name | 場景 |
| --- | --- | --- |
| 409 | Conflict | sourceOrderId 歧義（附 candidates）/ 狀態不可取消 |
| 502 | BadGateway | QPMN cancel API 調用失敗 |
| 500 | InternalServerError | 內部異常 |

---

## 3. 退貨（Return）

### `POST /api/oms/order/{source_order_id}/return`

#### Request Body

```json
{
  "reason": "damaged in transit",
  "isFullReturn": true,
  "trackingNumber": "1Z999AA10123456784",
  "carrierName": "FedEx",
  "trackingUrl": "https://www.fedex.com/fedextrack/?trknbr=1Z999AA10123456784"
}
```

| 字段 | 類型 | 必填 | 說明 |
| --- | --- | --- | --- |
| `reason` | string | 是 | 退貨原因 |
| `isFullReturn` | bool | 否 | 是否全單退貨，**默認 true** |
| `items` | array | 條件必填 | 退貨產品明細，**`isFullReturn=false` 時必填**（至少一行）|
| `items[].sku` | string | 是 | 退貨產品 SKU |
| `items[].quantity` | integer | 是 | 該 SKU 退貨數量（≥ 1）|
| `trackingNumber` | string | 否 | 退貨物流單號 |
| `carrierName` | string | 否 | 退貨承運商 |
| `trackingUrl` | string | 否 | 退貨物流跟蹤 URL |

**部分退貨示例**（`isFullReturn=false`，可多項）：

```json
{
  "reason": "customer changed mind on part of the order",
  "isFullReturn": false,
  "items": [
    { "sku": "PC-123", "quantity": 2 },
    { "sku": "PC-456", "quantity": 1 }
  ],
  "trackingNumber": "1Z999AA10123456784",
  "carrierName": "FedEx"
}
```

> `isFullReturn=false` 且未提供 `items`（或 `items` 為空）→ 422 校驗錯誤。

#### 處理邏輯

1. **僅 `shipped` 狀態可發起退貨**，其他狀態 → 409 Conflict（響應附當前狀態）
2. 已是 `returning` / `returncompleted` → **冪等返回 200**（`message: "Order return already requested"`）
3. 調用 QPMN return API（`PUT {QPMN_OPEN_API_URL}/orders/{store_order_id}/return`，Basic store_token 認證；**endpoint 待 QPMN 確認**），OMS 請求體原樣轉發
4. QPMN 成功 → 訂單轉 `returning`，記錄 `order_return_requested` 日誌（含完整退貨 payload）併發 warning 級通知郵件
5. QPMN 失敗 → 訂單保持 `shipped`，記錄 `qpmn_return_failed` 日誌，向 OMS 返回 502 BadGateway

#### 成功響應

```json
{
  "success": true,
  "message": "Order return requested successfully",
  "order_id": "6f8e...",
  "source_order_id": "ORD-123",
  "status": "returning"
}
```

#### 錯誤響應

| HTTP | Error Name | 場景 |
| --- | --- | --- |
| 404 | NotFound | 找不到 active 訂單 |
| 409 | Conflict | sourceOrderId 歧義 / 訂單非 shipped 狀態 |
| 422 | Unprocessable Entity | 請求體校驗失敗（如 `reason` 缺失、`isFullReturn=false` 未提供 `items`）|
| 502 | BadGateway | QPMN return API 調用失敗 |
| 500 | InternalServerError | 內部異常 |

---

## 4. 訂單狀態流轉（退貨相關）

```
shipped ──(OMS return API, QPMN 接受)──> returning ──(退貨完成)──> returncompleted
```

- `returning`（退貨）：SHIPPED 訂單成功發起退貨後的狀態，通知級別 warning
- `returncompleted`（退貨完成）：終端狀態，通知級別 info；**完成信號待 QPMN 定義反饋事件後接入**（狀態機已預留 `returning → returncompleted` 轉移）

OMS 狀態回推（API-002）詞彙同步新增 `returning` / `returncompleted`；這兩個狀態**不暴露給 VFS**（查詢 API 對外顯示為 `shipped`，postback 亦不推送 —— 退貨由 OMS 發起，VFS 不參與該流程）。

---

## 5. 審計與通知

- 每個 OMS 入站請求寫一筆 `webhook_logs`（`direction=inbound`、`source="OMS"`、完整 payload + 處理結果 PROCESSED/FAILED），對齊 QPMN 入站 webhook 的記錄模式
- 訂單級日誌：`oms_cancel_requested`（含取消原因）、`order_return_requested`（含完整退貨 payload）、`qpmn_return_failed`（QPMN 失敗詳情）
- 狀態變更郵件通知復用現有 per-client notification_config 機制

---

## 6. 待辦（依賴 QPMN）

- [ ] QPMN return API endpoint 最終確認後更新 `return_qpmn_order()` 中的 hard-coded 路徑（`app/services/order.py`）
- [ ] QPMN 退貨反饋事件定義後接入 `returncompleted` 觸發（webhook 處理器 + `EVENT_STATUS_MAP`）
