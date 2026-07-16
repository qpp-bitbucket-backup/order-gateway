# HP PrintOS API 认证使用指南

## ✅ 认证已启用

所有 API 端点现在都需要 **HP PrintOS API Key** 认证。

---

## 🔑 如何使用

### 方法一：X-API-Key Header（推荐）

```bash
curl -X POST http://localhost:8000/api/order/validate \
  -H "X-API-Key: hp-printos-api-key-change-in-production" \
  -H "Content-Type: application/json" \
  -d '{
    "destination": {"name": "test_account"},
    "orderData": {
      "sourceOrderId": "ORDER-001",
      "items": [{"sku": "BUSINESS-CARD"}]
    }
  }'
```

### 方法二：Bearer Token

```bash
curl -X GET http://localhost:8000/api/product \
  -H "Authorization: Bearer hp-printos-api-key-change-in-production"
```

---

## 📋 受保护的端点

以下所有端点都需要 API Key 认证：

### Orders API
- `POST /api/order/validate` - 验证订单
- `POST /api/order` - 提交订单
- `GET /api/order` - 获取订单列表
- `GET /api/order/{orderId}` - 获取订单详情
- `PUT /api/order/{sourceAccount}/{sourceOrderId}/cancel` - 取消订单

### Products API
- `GET /api/product` - 获取产品列表
- `GET /api/sku` - 获取 SKU 列表

### File Upload API
- `GET /api/file/getpreupload` - 获取文件上传 URL

### 公开端点（无需认证）
- `GET /` - 健康检查
- `GET /health` - 健康检查
- `GET /docs` - API 文档
- `GET /redoc` - ReDoc 文档

---

## 🔧 配置 API Key

### 开发环境

编辑 `.env` 文件：

```env
# HP PrintOS API Authentication
HP_API_KEY=hp-printos-api-key-change-in-production
HP_CLIENT_ID=hp-client-id-here
HP_CLIENT_SECRET=hp-client-secret-here
```

### 生产环境

使用环境变量或密钥管理服务：

```bash
export HP_API_KEY=your-secure-production-api-key
export HP_CLIENT_ID=your-client-id
export HP_CLIENT_SECRET=your-client-secret
```

---

## 🧪 测试认证

### 测试 1: 无 API Key（应该失败）

```bash
curl -X GET http://localhost:8000/api/product
```

**预期响应**:
```json
{
  "detail": "API key is required"
}
```

**HTTP Status**: `401 Unauthorized`

### 测试 2: 错误的 API Key（应该失败）

```bash
curl -X GET http://localhost:8000/api/product \
  -H "X-API-Key: wrong-key"
```

**预期响应**:
```json
{
  "detail": "Invalid API key"
}
```

**HTTP Status**: `403 Forbidden`

### 测试 3: 正确的 API Key（应该成功）

```bash
curl -X GET http://localhost:8000/api/product \
  -H "X-API-Key: hp-printos-api-key-change-in-production"
```

**预期响应**:
```json
{
  "success": true,
  "count": 0,
  "page": 1,
  "pages": 1,
  "data": []
}
```

**HTTP Status**: `200 OK`

---

## 📝 Python 客户端示例

### 使用 requests

```python
import requests

API_KEY = "hp-printos-api-key-change-in-production"
BASE_URL = "http://localhost:8000"

headers = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

# 验证订单
response = requests.post(
    f"{BASE_URL}/api/order/validate",
    headers=headers,
    json={
        "destination": {"name": "test_account"},
        "orderData": {
            "sourceOrderId": "ORDER-001",
            "items": [{"sku": "BUSINESS-CARD"}]
        }
    }
)

print(response.json())
```

### 使用 httpx（异步）

```python
import httpx
import asyncio

API_KEY = "hp-printos-api-key-change-in-production"
BASE_URL = "http://localhost:8000"

async def submit_order():
    headers = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BASE_URL}/api/order",
            headers=headers,
            json={
                "destination": {"name": "test_account"},
                "orderData": {
                    "sourceOrderId": "ORDER-001",
                    "items": [{"sku": "BUSINESS-CARD"}]
                }
            }
        )
        
        return response.json()

result = asyncio.run(submit_order())
print(result)
```

---

## 🔐 Webhook 签名验证

对于接收 HP PrintOS webhook 通知，需要验证签名：

```python
from fastapi import Request, Depends
from app.core.auth_hp import verify_hp_webhook_signature

@router.post("/webhook")
async def hp_webhook(
    request: Request,
    verified: bool = Depends(verify_hp_webhook_signature)
):
    # Webhook 已验证，可以安全处理
    data = await request.json()
    
    # 处理 webhook 数据
    print("Received webhook:", data)
    
    return {"status": "received"}
```

**HP PrintOS Webhook Headers**:
- `X-HP-Signature`: HMAC-SHA256 签名
- `X-HP-Timestamp`: Unix 时间戳

---

## 🚨 错误代码

| HTTP 状态码 | 错误代码 | 说明 |
|------------|---------|------|
| 401 | MISSING_API_KEY | 缺少 API Key |
| 401 | EXPIRED_TIMESTAMP | 请求时间戳过期（Webhook） |
| 403 | INVALID_API_KEY | API Key 无效 |
| 403 | INVALID_SIGNATURE | Webhook 签名无效 |

---

## 🔄 轮换 API Key

### 步骤

1. **生成新 Key**
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

2. **更新配置**
   ```env
   HP_API_KEY=new-generated-api-key-here
   ```

3. **重启服务**
   ```bash
   docker-compose restart app
   ```

4. **更新所有客户端**
   - 更新所有使用旧 API Key 的集成

5. **监控旧 Key 的使用**
   - 确保没有客户端仍在使用旧 Key

---

## 📊 API 文档中的认证

访问 Swagger UI: http://localhost:8000/docs

1. 点击右上角 **"Authorize"** 按钮
2. 输入您的 API Key
3. 点击 **"Authorize"**
4. 现在所有请求都会自动包含认证头

---

## 🛡️ 安全最佳实践

### 1. 不要硬编码 API Key

❌ **错误**:
```python
headers = {"X-API-Key": "my-secret-key"}
```

✅ **正确**:
```python
import os
API_KEY = os.getenv("HP_API_KEY")
```

### 2. 使用 HTTPS

生产环境始终使用 HTTPS：
```env
# .env.production
HOST=0.0.0.0
PORT=443
```

### 3. 限制 API Key 权限

为不同的集成创建不同的 API Key：
```env
HP_API_KEY_WEBHOOK=webhook-specific-key
HP_API_KEY_ORDER_SUBMIT=order-submission-key
```

### 4. 监控和日志记录

记录所有认证失败的请求：
```python
from app.core.auth_hp import verify_hp_api_key
import logging

logger = logging.getLogger(__name__)

@router.get("/endpoint")
def endpoint(api_key: str = Depends(verify_hp_api_key)):
    logger.info(f"Request authenticated with key: {api_key[:8]}...")
    # 处理请求
```

### 5. 速率限制

实施速率限制防止滥用：
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.get("/orders")
@limiter.limit("100/minute")
def list_orders(request: Request, api_key: str = Depends(verify_hp_api_key)):
    pass
```

---

## 🆘 故障排除

### 问题 1: 401 Unauthorized

**可能原因**:
- 未提供 API Key
- Header 名称错误

**解决方案**:
```bash
# 确保使用正确的 header 名称
curl -H "X-API-Key: your-key" ...
```

### 问题 2: 403 Forbidden

**可能原因**:
- API Key 不正确
- API Key 已过期

**解决方案**:
1. 检查 `.env` 中的 `HP_API_KEY` 值
2. 确保客户端使用的 Key 与服务器配置一致

### 问题 3: 认证在本地工作但 Docker 中失败

**可能原因**:
- Docker 环境变量未正确设置

**解决方案**:
```bash
# 检查 Docker 环境变量
docker-compose exec app env | grep HP_API_KEY

# 或在 docker-compose.yml 中显式设置
environment:
  - HP_API_KEY=${HP_API_KEY}
```

---

## 📚 更多信息

- **Swagger 文档**: http://localhost:8000/docs
- **ReDoc 文档**: http://localhost:8000/redoc
- **HP Developer Portal**: https://developers.hp.com/printos

---

## ✨ 快速开始

```bash
# 1. 设置 API Key
echo 'HP_API_KEY=my-test-key' >> .env

# 2. 重启服务
python -m uvicorn app.main:app --reload

# 3. 测试
curl http://localhost:8000/api/product \
  -H "X-API-Key: my-test-key"

# 4. 查看文档
open http://localhost:8000/docs
```

完成！🎉 您的 API 现在已受到 HP PrintOS 风格的认证保护。
