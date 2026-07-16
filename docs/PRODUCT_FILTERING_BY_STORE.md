# 产品 API Store ID 过滤功能

## 📖 概述

`/api/product` 和 `/api/sku` 端点现在会根据认证客户端的 `store_id` 自动过滤返回的产品和 SKU 数据。

## 🔐 工作原理

### 认证流程

```
客户端请求
    ↓
OneFlow 认证 (verify_oneflow_auth)
    ↓
获取客户端 token
    ↓
查询 clients 表获取 store_id
    ↓
使用 store_id 过滤 products/skus
    ↓
返回过滤后的结果
```

### 两种认证模式

#### 1. **客户端认证（推荐）**
- 使用在 `clients` 表中注册的 token
- API 会自动根据该客户端的 `store_id` 过滤数据
- 只返回属于该店铺的产品和 SKU

#### 2. **Bootstrap 认证（开发用）**
- 使用 `.env` 中的 `ONEFLOW_TOKEN` 和 `ONEFLOW_SECRET`
- 没有关联的 `store_id`
- 返回所有店铺的产品和 SKU（不过滤）

## 🎯 API 端点

### GET /api/product

获取产品列表，根据客户端的 store_id 自动过滤。

**请求示例：**
```bash
curl -X GET "http://localhost:8000/api/product?page=1&pagesize=20" \
  -H "x-oneflow-authorization: YOUR_TOKEN:SIGNATURE" \
  -H "x-oneflow-date: 2026-06-22T12:00:00.000Z" \
  -H "x-oneflow-algorithm: SHA256"
```

**响应示例：**
```json
{
  "success": true,
  "count": 50,
  "page": 1,
  "pages": 3,
  "data": [
    {
      "id": "prod_123",
      "productCode": "BUSINESS_CARD",
      "description": "Business Card",
      "components": [...]
    }
  ]
}
```

### GET /api/sku

获取 SKU 列表，根据客户端的 store_id 自动过滤。

**请求示例：**
```bash
curl -X GET "http://localhost:8000/api/sku?page=1&pagesize=20" \
  -H "x-oneflow-authorization: YOUR_TOKEN:SIGNATURE" \
  -H "x-oneflow-date: 2026-06-22T12:00:00.000Z" \
  -H "x-oneflow-algorithm: SHA256"
```

**响应示例：**
```json
{
  "success": true,
  "count": 150,
  "page": 1,
  "pages": 8,
  "data": [
    {
      "id": "sku_456",
      "code": "BC_001",
      "description": "Business Card - Standard",
      "productId": "prod_123",
      "active": true,
      "unitPrice": 0.50,
      "unitCost": 0.30
    }
  ]
}
```

## 📋 设置步骤

### 1. 创建客户端

首先为店铺创建客户端配置：

```bash
curl -X POST "http://localhost:8000/api/client" \
  -H "x-admin-key: admin-api-key-change-in-production" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Store Client",
    "store_id": "STORE123",
    "store_key": "my-store-key-here",
    "description": "Client for STORE123"
  }'
```

响应会包含生成的 `token` 和 `secret`：
```json
{
  "success": true,
  "client": {
    "id": 1,
    "name": "My Store Client",
    "store_id": "STORE123",
    "token": "generated_token_here",
    "description": "Client for STORE123",
    "is_active": true,
    "created_at": "2026-06-22T12:00:00",
    "updated_at": "2026-06-22T12:00:00"
  },
  "secret": "generated_secret_here"
}
```

### 2. 生成签名

使用返回的 `token` 和 `secret` 生成 OneFlow 签名：

```python
import hmac
import hashlib
from datetime import datetime,timezone, timezone

def generate_signature(secret, method, path, timestamp):
    string_to_sign = f"{method.upper()} {path} {timestamp}"
    return hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

# 示例
token = "generated_token_here"
secret = "generated_secret_here"
timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
signature = generate_signature(secret, "GET", "/api/product", timestamp)
auth_header = f"{token}:{signature}"
```

### 3. 调用 API

```bash
curl -X GET "http://localhost:8000/api/product" \
  -H "x-oneflow-authorization: generated_token_here:SIGNATURE" \
  -H "x-oneflow-date: TIMESTAMP" \
  -H "x-oneflow-algorithm: SHA256"
```

## 🔍 过滤逻辑

### Products 过滤

```sql
-- 如果客户端有 store_id
SELECT * FROM products 
WHERE is_active = true 
  AND store_id = 'STORE123';

-- 如果使用 bootstrap credentials
SELECT * FROM products 
WHERE is_active = true;
```

### SKUs 过滤

```sql
-- 如果客户端有 store_id
SELECT skus.* FROM skus
JOIN products ON skus.product_id = products.product_id
WHERE skus.active = true 
  AND products.store_id = 'STORE123';

-- 如果使用 bootstrap credentials
SELECT * FROM skus 
WHERE active = true;
```

## 💡 使用场景

### 场景 1：多租户隔离

不同店铺的客户端只能看到自己店铺的产品：

```
Client A (store_id="STORE_A") → 只看到 STORE_A 的产品
Client B (store_id="STORE_B") → 只看到 STORE_B 的产品
```

### 场景 2：共享产品库

某些产品可能对所有店铺可用（store_id 为 NULL）：

```python
# 如果需要支持共享产品，可以修改过滤逻辑
if store_id:
    query = query.where(
        or_(
            Product.store_id == store_id,
            Product.store_id == None
        )
    )
```

### 场景 3：开发和测试

使用 bootstrap credentials 查看所有产品：

```bash
# 使用 .env 中的 ONEFLOW_TOKEN
curl -X GET "http://localhost:8000/api/product" \
  -H "x-oneflow-authorization: oneflow-token-change-in-production:SIGNATURE" \
  -H "x-oneflow-date: TIMESTAMP" \
  -H "x-oneflow-algorithm: SHA256"
```

## 🛠️ 技术实现

### 1. 认证依赖函数

`app/core/auth_oneflow.py` 中新增：

```python
async def get_client_store_id(
    request: Request,
    session: Session = Depends(get_session),
    x_oneflow_authorization: Optional[str] = Security(...),
    x_oneflow_date: Optional[str] = Security(...),
    x_oneflow_algorithm: Optional[str] = Security(...),
) -> Optional[str]:
    """
    Verify OneFlow auth and return the client's store_id.
    Returns None if using bootstrap credentials.
    """
    token = await verify_oneflow_auth(...)
    client = _get_client_by_token(session, token)
    
    if client:
        return client.store_id
    
    return None
```

### 2. API 端点使用

```python
@router.get("/product", response_model=ProductsListResponse)
def get_products(
    page: int = Query(1, ge=1),
    pagesize: int = Query(100, ge=1, le=1000),
    session: Session = Depends(get_session),
    store_id: Optional[str] = Depends(get_client_store_id),  # 自动注入
):
    query = select(Product).where(Product.is_active == True)
    
    if store_id:
        query = query.where(Product.store_id == store_id)
    
    # ... 执行查询
```

## ⚠️ 注意事项

### 1. 数据库要求

确保产品和 SKU 已正确设置 `store_id`：

```sql
-- 检查产品的 store_id
SELECT product_id, product_code, store_id 
FROM products 
LIMIT 10;

-- 更新缺失的 store_id
UPDATE products 
SET store_id = 'STORE123' 
WHERE product_id = 'prod_123';
```

### 2. 客户端配置

每个客户端必须有正确的 `store_id`：

```sql
SELECT id, name, store_id, token, is_active 
FROM clients;
```

### 3. Bootstrap Credentials

`.env` 中的 bootstrap credentials 没有 `store_id`，会返回所有数据：

```env
ONEFLOW_TOKEN=oneflow-token-change-in-production
ONEFLOW_SECRET=oneflow-secret-change-in-production
```

**建议：** 生产环境中禁用或限制 bootstrap credentials 的使用。

### 4. 性能优化

- `products.store_id` 和 `skus` 通过 `product_id` join 都有索引
- 分页查询已经优化
- 大数据量时考虑添加缓存

## 🐛 故障排查

### 问题 1：返回空列表

**可能原因：**
- 该 store_id 没有活跃的产品
- 客户端的 store_id 配置错误

**解决方案：**
```sql
-- 检查是否有产品
SELECT COUNT(*) FROM products 
WHERE is_active = true AND store_id = 'STORE123';

-- 检查客户端配置
SELECT store_id FROM clients WHERE token = 'YOUR_TOKEN';
```

### 问题 2：认证失败

**可能原因：**
- Token 或 Secret 错误
- 签名计算错误
- 时间戳过期

**解决方案：**
- 检查 token 和 secret 是否正确
- 验证签名计算逻辑
- 确保时间戳在 5 分钟内

### 问题 3：看到其他店铺的产品

**可能原因：**
- 使用了 bootstrap credentials
- store_id 过滤未生效

**解决方案：**
- 确认使用的是客户端 token，不是 ONEFLOW_TOKEN
- 检查日志确认 store_id 被正确提取

## 📊 监控和日志

### 查看 API 调用日志

```python
# 在 auth_oneflow.py 中添加日志
logger.info(f"Client token: {token}, store_id: {store_id}")
```

### 数据库查询分析

```sql
-- 查看各店铺的产品数量
SELECT store_id, COUNT(*) as product_count
FROM products
WHERE is_active = true
GROUP BY store_id;

-- 查看各店铺的 SKU 数量
SELECT p.store_id, COUNT(*) as sku_count
FROM skus s
JOIN products p ON s.product_id = p.product_id
WHERE s.active = true
GROUP BY p.store_id;
```

## ✨ 最佳实践

1. **为每个店铺创建独立客户端**：不要共享 token
2. **定期轮换密钥**：使用 rotate_secret 功能
3. **监控使用情况**：跟踪每个客户端的 API 调用
4. **限制权限**：只授予必要的访问权限
5. **测试过滤逻辑**：确保不同店铺看到正确的数据

## 🔗 相关文档

- [OneFlow 认证指南](AUTH_GUIDE.md)
- [客户端管理 API](API_AUTHENTICATION_GUIDE.md)
- [产品同步指南](CELERY_WORKER_GUIDE.md)

---

**提示：** 如有问题，请检查客户端配置和数据库中的 store_id 字段。
