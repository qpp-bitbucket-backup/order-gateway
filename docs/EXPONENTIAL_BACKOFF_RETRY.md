# 失敗訂單重試隊列 — 指數退避機制

## 概述

當訂單流程中調用外部 API（OMS / QPMN）遇到 **503** 或 **超時** 等暫時性錯誤時，系統會自動將任務延遲後重試，採用**指數退避（Exponential Backoff）**策略，避免對故障服務造成重複壓力。重試次數耗盡後仍失敗，則將訂單狀態標記為 `failed`。

## 適用場景

| 任務 | 觸發條件 | 涉及 API |
|------|---------|----------|
| `validate_order` | OMS API 返回 503 或請求超時 | OMS API-001 `/order/addresses` |
| `push_order` | QPMN API 返回 503 | QPMN `/store/orders` |
| `push_order` | QPMN API 請求超時 | QPMN `/store/orders` |

## 重試公式

```
delay = min(base_delay × 2^retry_count, max_delay)
```

- `retry_count`：當前已重試次數（從 0 開始）
- `base_delay`：基礎延遲秒數（環境變量配置）
- `max_delay`：最大延遲上限秒數（環境變量配置）

## 環境變量配置

### OMS 驗證重試（validate_order）

| 環境變量 | 說明 | 默認值 |
|---------|------|-------|
| `OMS_VALIDATE_RETRY_COUNT` | 最大重試次數 | `5` |
| `OMS_VALIDATE_RETRY_COUNTDOWN` | 基礎延遲秒數 | `300`（5 分鐘） |
| `OMS_VALIDATE_RETRY_MAX_COUNTDOWN` | 最大延遲上限秒數 | `3600`（1 小時） |

### QPMN 推送重試（push_order）

| 環境變量 | 說明 | 默認值 |
|---------|------|-------|
| `QPMN_PUSH_RETRY_COUNT` | 最大重試次數 | `5` |
| `QPMN_PUSH_RETRY_COUNTDOWN` | 基礎延遲秒數 | `900`（15 分鐘） |
| `QPMN_PUSH_RETRY_MAX_COUNTDOWN` | 最大延遲上限秒數 | `7200`（2 小時） |

## 實際延遲效果

| 重試次數 | OMS 延遲（base=300, cap=3600） | QPMN 延遲（base=900, cap=7200） |
|---------|-------------------------------|--------------------------------|
| 第 1 次 | 300s（5 分鐘） | 900s（15 分鐘） |
| 第 2 次 | 600s（10 分鐘） | 1800s（30 分鐘） |
| 第 3 次 | 1200s（20 分鐘） | 3600s（1 小時） |
| 第 4 次 | 2400s（40 分鐘） | 7200s（2 小時，觸頂） |
| 第 5 次 | 3600s（1 小時，觸頂） | 7200s（2 小時，觸頂） |

## 重試流程

```
任務執行 → 調用外部 API
    ├── 成功 → 繼續後續流程
    └── 失敗（503 / 超時）
        ├── retry_count < max_retries
        │   ├── 計算指數退避延遲
        │   ├── 記錄重試日誌到 order.logs
        │   └── 延遲 countdown 秒後重新投遞 Celery 任務
        └── retry_count >= max_retries
            ├── 記錄錯誤日誌
            └── 調用 _mark_order_failed() 將訂單狀態設為 FAILED
```

## 重試次數追蹤

重試次數通過 `order_data` 字典中的內部欄位追蹤，不會持久化到資料庫：

- OMS：`order_data["_oms_retry_count"]`
- QPMN：`order_data["_qpmn_retry_count"]`

每次重試時 Celery 任務會將計數器 +1 後隨 `order_data` 一併傳遞給下一次投遞。

## 涉及文件

| 文件 | 說明 |
|------|------|
| `app/core/config.py` | 環境變量定義（重試次數、基礎延遲、最大延遲） |
| `app/tasks/orders.py` | `_exponential_backoff()` 工具函數 + `validate_order` / `push_order` 重試邏輯 |
| `app/services/oms.py` | `OMSRetryableError` 自定義異常，識別 503 / 超時 |
| `.env.example` | 環境變量範本 |

## 核心代碼

### 指數退避計算函數

```python
# app/tasks/orders.py
def _exponential_backoff(base: int, retry_count: int, cap: int) -> int:
    """Calculate exponential backoff delay: min(base * 2^retry_count, cap)."""
    return min(base * (2 ** retry_count), cap)
```

### OMS 重試（validate_order）

```python
except OMSRetryableError as oms_exc:
    retry_count = order_data.get("_oms_retry_count", 0)
    max_retries = settings.OMS_VALIDATE_RETRY_COUNT
    base_delay = settings.OMS_VALIDATE_RETRY_COUNTDOWN
    max_delay = settings.OMS_VALIDATE_RETRY_MAX_COUNTDOWN
    if retry_count < max_retries:
        countdown = _exponential_backoff(base_delay, retry_count, max_delay)
        order_data["_oms_retry_count"] = retry_count + 1
        # ... 記錄日誌 ...
        validate_order.apply_async(args=[order_data], countdown=countdown)
        return True
    else:
        _mark_order_failed(order_id, f"OMS API failed after {max_retries} retries: {oms_exc}")
        return False
```

### QPMN 重試（push_order — 503 和超時共用相同邏輯）

```python
# 503 處理
if response.status_code == 503:
    if retry_count < max_retries:
        countdown = _exponential_backoff(base_delay, retry_count, max_delay)
        order_data["_qpmn_retry_count"] = retry_count + 1
        # ... 記錄日誌 ...
        push_order.apply_async(args=[order_data], countdown=countdown)
        return True
    else:
        _mark_order_failed(order_id, f"QPMN returned 503 after {max_retries} retries")
        return False

# 超時處理
except httpx.TimeoutException:
    if retry_count < max_retries:
        countdown = _exponential_backoff(base_delay, retry_count, max_delay)
        order_data["_qpmn_retry_count"] = retry_count + 1
        # ... 記錄日誌 ...
        push_order.apply_async(args=[order_data], countdown=countdown)
        return True
    else:
        _mark_order_failed(order_id, f"QPMN timeout after {max_retries} retries")
        return False
```
