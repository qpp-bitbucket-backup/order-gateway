# 訂單冷靜期(COOLING_OFF)新狀態變更說明

## 概述

本次變更(TI-70)為訂單新增內部狀態 `COOLING_OFF`(冷靜期)。當 client(店鋪)配置了冷靜期秒數後,訂單通過驗證(`VALIDATED`)不會立即推送 QPMN,而是先進入 `cooling_off` 狀態等待指定秒數,之後才執行推送。此機制給予買家一段反悔窗口(冷靜期內可取消/修改訂單),適用於有法定冷靜期要求或業務需要的店鋪。

- **提交**: `27a3451` — `TI-70: add order cooling-off status`(PR #120,feature/TI-70,2026-09-10)
- **核心原則**: `COOLING_OFF` 與 `PENDING`/`PROCESSING` 一樣為**內部專用狀態**,絕不通知 OMS/VFS,對外仍映射為 `dataready`。

## 資料庫變更

遷移檔:[`c9d0e1f2a3b4_add_cooling_off_status_and_client_cooling_off.py`](../alembic/versions/2026_09_10_1030-c9d0e1f2a3b4_add_cooling_off_status_and_client_cooling_off.py)

| 變更 | 表 | 說明 |
|------|-----|------|
| ENUM 擴充 | `orders.status` | 新增 `cooling_off` 值,位於 `validated` 與 `processing` 之間(MySQL 需重建 ENUM 欄位) |
| 新增欄位 | `clients.cooling_off_seconds` | `INT NOT NULL DEFAULT 0`,店鋪級冷靜期配置(0 = 無冷靜期) |
| 新增欄位 | `orders.cooling_off_seconds` | `INT NULL`,訂單進入冷靜期時實際套用秒數的**快照**(NULL = 從未進入冷靜期) |

完整 ENUM 順序:`received → pending → validated → cooling_off → processing → printready → printed → produced → cancelled → failed → errored → shipped`

> **降級注意**: 執行 downgrade 前應先推送或取消所有仍處於 `cooling_off` 的訂單,否則 ENUM 縮回時該狀態將無法容納。

## 狀態機變更([app/models/order.py](../app/models/order.py))

- `OrderStatus` 枚舉新增 `COOLING_OFF = "cooling_off"`,插入於 `VALIDATED` 與 `PROCESSING` 之間。
- `ORDER_STATE_TRANSITIONS` 調整:

```
VALIDATED ──┬──→ COOLING_OFF(配置了冷靜期時)──┬──→ PROCESSING(冷靜期結束,進入處理中)
            │                                ├──→ CANCELLED(冷靜期內可取消訂單)
            ├──→ PROCESSING(無冷靜期直接處理)  ├──→ FAILED(推送準備失敗)
            └──→ CANCELLED / FAILED / ERRORED └──→ ERRORED(冷靜期過程異常)
```

- `COOLING_OFF` **不可**直接跳轉至 `PRINTREADY` 等列印階段狀態。
- `COOLING_OFF` 不在 `OMS_STATUS_MAP` 與 `STATUS_EVENT_MAP` 中,即不會產生任何對外狀態通知(QPMN 確認不發送 DataReady 事件,先前的 dataready 通知已涵蓋此階段)。

## 核心流程變更([app/tasks/orders.py](../app/tasks/orders.py))

`validate_order` Celery 任務在驗證通過後、鏈結 `push_order` 之前:

1. 呼叫 `client_service.get_cooling_off_seconds(order.store_id)` 查詢店鋪冷靜期配置。
2. 若 `cooling_off_seconds > 0`:
   - 訂單狀態更新為 `COOLING_OFF` 並提交;
   - 將實際套用的秒數**快照**至 `order.cooling_off_seconds`(供平台前端結合 `order_cooling_off` 日誌時間戳渲染倒數計時,並在冷靜期結束後保留作為記錄);
   - 寫入訂單日誌事件 `order_cooling_off`(`Order holding in cooling-off for {N}s before QPMN push`)。
3. `push_order.apply_async` 以 `countdown=cooling_off_seconds` 延遲排程;冷靜期結束後任務自動觸發,訂單進入 `PROCESSING` 並推送 QPMN。
4. 若冷靜期為 0(未配置/未知店鋪),行為與原本完全一致,立即推送。

## 訂單可編輯性([app/services/order.py](../app/services/order.py))

`UPDATABLE_STATUSES` 新增 `COOLING_OFF`。冷靜期內訂單尚未推送 QPMN,仍屬可安全編輯階段,可接受更新與取消操作。

## Client API 變更([app/api/clients.py](../app/api/clients.py)、[app/schemas/client.py](../app/schemas/client.py))

冷靜期以店鋪(client)為單位配置:

| 接口 | 變更 |
|------|------|
| `ClientCreateRequest` / `PlatformClientCreateRequest` | 新增可選欄位 `cooling_off_seconds`(`int ≥ 0`,省略或 0 = 無冷靜期) |
| `ClientUpdateRequest` | 新增可選欄位 `cooling_off_seconds`(傳入即更新) |
| `ClientSummary` | 回應新增 `cooling_off_seconds`(`int`,預設 0) |

管理端(`create_client` / `update_client`)與平台端(`platform_create_client` / `platform_update_client`)均支援該欄位的讀寫。

## 平台訂單 API 變更([app/api/orders.py](../app/api/orders.py)、[app/schemas/order.py](../app/schemas/order.py))

- `PlatformOrderSummary` 與 `PlatformFullOrder` 新增 `coolingOffSeconds` 欄位(`int,可為 NULL`),即訂單實際套用的冷靜期快照。前端可搭配訂單日誌中 `order_cooling_off` 事件的時間戳渲染冷靜期倒數。
- 平台訂單列表與詳情接口(`platform_get_orders` / `platform_get_order`)均回傳該欄位。

## 對外狀態映射(不變的關鍵保證)

`_EXTERNAL_STATUS_MAP` 新增一行:

| 內部狀態 | 對外狀態 |
|----------|----------|
| `COOLING_OFF` | `dataready` |

即冷靜期訂單對外部調用方(OMS/VFS/平台)呈現的狀態與 `VALIDATED`/`PROCESSING` 一致,外部系統**無需任何改動**,也感知不到此內部階段的存在。

## 測試覆蓋

| 測試檔 | 驗證內容 |
|--------|----------|
| `tests/test_order_model.py` | `cooling_off` 枚舉值;`VALIDATED → COOLING_OFF → PROCESSING/CANCELLED` 轉換合法且不可跳至 `PRINTREADY`;`COOLING_OFF` 不在 `OMS_STATUS_MAP`/`STATUS_EVENT_MAP`(internal-only);`cooling_off_seconds` 快照欄位(預設 NULL) |
| `tests/test_order_service_helpers.py` | `COOLING_OFF` 已加入 `UPDATABLE_STATUSES` |
| `tests/test_orders_api_helpers.py` | `_EXTERNAL_STATUS_MAP[COOLING_OFF] == "dataready"` |

## 部署與運維注意事項

1. **先執行資料庫遷移**再部署新代碼(`alembic upgrade head`),ENUM 擴充與新欄位均為向後相容。
2. Celery worker 需重啟以載入新任務邏輯;`countdown` 延遲依賴 broker(RabbitMQ)正常運作。
3. 若降級版本,先處理(推送或取消)所有 `cooling_off` 狀態訂單,再執行 downgrade。
4. 冷靜期配置即時生效:修改 client 的 `cooling_off_seconds` 只影響之後驗證通過的訂單;已進入冷靜期的訂單以其快照值為準。
