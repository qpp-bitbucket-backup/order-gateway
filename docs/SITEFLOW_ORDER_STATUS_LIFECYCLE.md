# HP SiteFlow / OneFlow 訂單狀態生命週期

## 概述

HP SiteFlow（前身為 OneFlow）的 `orderData.status` 字段記錄訂單在生產流程中的當前階段。
本文檔整理了官方確認的狀態值、含義及生命週期階段。

## orderData.status 狀態值

| Status | 含義 | 生命週期階段 | 終端狀態 |
|--------|------|-------------|----------|
| `received` | 訂單已接收 | 初始狀態 | 否 |
| `accepted` | 訂單已接受（等同 `received`，部分 API 版本使用） | 初始狀態 | 否 |
| `dataready` | 數據準備就緒 | 預處理 | 否 |
| `printready` | 列印就緒 | 印前完成 | 否 |
| `printed` | 已列印 | 生產完成 | 否 |
| `shipped` | 已出貨 | 出貨 | 是 |
| `cancelled` | 已取消 | 終止 | 是 |
| `onhold` | 暫停中（等待處理或人工介入） | 異常分支 | 否 |
| `error` | 錯誤（生產過程中出錯） | 異常狀態 | 否 |

## 生命週期流程圖

```
                  ┌──────────┐
                  │ received │ ← 訂單提交後初始狀態
                  └────┬─────┘
                       │
                       ▼
                  ┌──────────┐
                  │ dataready│ ← 數據準備就緒
                  └────┬─────┘
                       │
                       ▼
                  ┌───────────┐
                  │ printready│ ← 列印就緒
                  └────┬──────┘
                       │
                       ▼
                  ┌──────────┐
                  │ printed  │ ← 列印完成
                  └────┬─────┘
                       │
                       ▼
                  ┌──────────┐
                  │ shipped  │ ← 出貨（終端狀態）
                  └──────────┘

  異常分支:
    任意狀態 ──→ onhold   （暫停，待人工介入後恢復）
    任意狀態 ──→ error    （錯誤，可重試或轉為 cancelled）
    任意狀態 ──→ cancelled（取消，終端狀態）
```

## 本項目的狀態映射

項目內部使用 `OrderStatus` 枚舉（`app/models/order.py`），通過
`_EXTERNAL_STATUS_MAP`（`app/api/orders.py`）映射到 SiteFlow 外部狀態：

| 內部狀態 (OrderStatus) | 外部狀態 (orderData.status) | 說明 |
|------------------------|-----------------------------|------|
| `RECEIVED` | `received` | 直接映射 |
| `PENDING` | `received` | 合併到 `received` |
| `VALIDATED` | `dataready` | 驗證通過 |
| `PROCESSING` | `dataready` | 處理中，合併到 `dataready` |
| `PRINTREADY` | `printready` | 直接映射 |
| `PRINTED` | `printed` | 直接映射 |
| `SHIPPED` | `shipped` | 直接映射 |
| `CANCELLED` | `cancelled` | 直接映射 |
| `ERRORED` | `error` | 直接映射 |
| `FAILED` | `error` | 合併到 `error` |

> **注意**：項目內部沒有 `onhold` 對應的內部狀態。

## 注意事項

1. **`accepted` vs `received`**：HP 範例文檔中使用 `<accepted>` 作為 placeholder，
   但實際系統和 SDK 中主要使用 `received`。兩者可能在不同的 SiteFlow API 版本中交替出現。

2. **`orderData.status` 非原生字段**：SiteFlow 原生 API 響應**不包含**
   `orderData.status` 字段。當前項目通過 `_enrich_order_data_with_status()`
   注入此字段以滿足 VFS（客戶端）對狀態的查詢需求。

3. **Item/Shipment 級別的 `status`**：`orderData.items[].status` 和
   `orderData.shipments[].status` 使用**不同的值空間**（如 `live`），
   與 `orderData.status` 的訂單生命週期值不同。

4. **資料來源**：
   - `docs/examples/siteflow_data_examples.md` — HP 提供的範例文檔
   - `docs/examples/siteflow_data_examples-order_status_response-success.json` — 實際 SiteFlow 響應
   - HP SiteFlow API 官方文檔（`hpsiteflow.com/docs/api-reference/siteflow-pro.html`，需認證）
   - HP SiteFlow SDK 開源代碼（[GitHub](https://github.com/HPInc/printos-siteflow-api-samples)）
