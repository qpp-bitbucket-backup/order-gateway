# 手动同步产品脚本使用指南

## 📖 概述

`sync_products.py` 是一个命令行工具，用于手动从 QPMN API 同步产品和 SKU 数据到数据库。

## 🚀 基本用法

### 1. 同步特定店铺的产品和 SKU

```bash
python sync_products.py --store-id STORE123
```

或使用短参数：
```bash
python sync_products.py -s STORE123
```

### 2. 仅同步 SKU（不同步产品）

```bash
python sync_products.py --store-id STORE123 --skus-only
```

### 3. 查看帮助信息

```bash
python sync_products.py --help
```

输出：
```
usage: sync_products.py [-h] -s STORE_ID [--skus-only]

Manual Product Sync from QPMN API

options:
  -h, --help            show this help message and exit
  -s STORE_ID, --store-id STORE_ID
                        Store ID to sync products for (required)
  --skus-only           Sync only SKUs, not products

Examples:
  # Sync products for a specific store
  python sync_products.py --store-id STORE123
  
  # Sync only SKUs for a specific store
  python sync_products.py --store-id STORE123 --skus-only
  
  # Short form
  python sync_products.py -s STORE123
```

## 📋 前置要求

### 1. 确保 RabbitMQ 正在运行

```bash
# 检查 RabbitMQ 状态（Docker）
docker ps | grep rabbitmq

# 或检查服务状态（Linux）
sudo systemctl status rabbitmq-server
```

如果未运行，启动 RabbitMQ：
```bash
# Ubuntu/Debian
sudo systemctl start rabbitmq-server

# 或使用 Docker
docker start rabbitmq

# Windows (使用 Docker)
docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3-management
```

### 2. 确保虚拟环境已激活

```bash
# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### 3. 确保 clients 表中有 store_key

在运行同步之前，确保 `clients` 表中已经有对应 `store_id` 的记录，并且包含 `store_key`：

```sql
-- 检查 store_key 是否存在
SELECT store_id, store_key FROM clients WHERE store_id = 'STORE123';
```

如果不存在，需要先创建客户端：

```bash
curl -X POST "http://localhost:8000/api/client" \
  -H "x-admin-key: admin-api-key-change-in-production" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Store",
    "store_id": "STORE123",
    "store_key": "your-store-key-here",
    "description": "Test store for product sync"
  }'
```

## 💡 使用示例

### 示例 1：同步单个店铺

```bash
python sync_products.py -s STORE123
```

输出：
```
============================================================
Manual Product Sync from QPMN API
============================================================

Store ID: STORE123
Mode: Products and SKUs

Starting sync...

Syncing products and SKUs...

============================================================
Sync Results:
============================================================
Success: True
Products synced: 50
SKUs synced: 150
Timestamp: 2026-06-22T12:00:00.000Z
============================================================
```

### 示例 2：仅同步 SKU

```bash
python sync_products.py -s STORE456 --skus-only
```

输出：
```
============================================================
Manual Product Sync from QPMN API
============================================================

Store ID: STORE456
Mode: SKUs only

Starting sync...

Syncing SKUs...

============================================================
Sync Results:
============================================================
Success: True
SKUs synced: 75
Timestamp: 2026-06-22T12:05:00.000Z
============================================================
```

### 示例 3：错误处理

如果找不到对应的客户端：

```bash
python sync_products.py -s NONEXISTENT
```

输出：
```
============================================================
Manual Product Sync from QPMN API
============================================================

Store ID: NONEXISTENT
Mode: Products and SKUs

Starting sync...

Syncing products and SKUs...

============================================================
Sync Results:
============================================================
Success: False
Message: No client found for store_id: NONEXISTENT
Products synced: 0
SKUs synced: 0
============================================================
```

## 🔍 工作流程

```
运行脚本 (store_id="STORE123")
    ↓
查询 clients 表
    ↓
获取 store_key
    ↓
调用 QPMN API
GET /store/STORE123/products
Authorization: Basic {store_key}
    ↓
解析响应数据
    ↓
同步到数据库
  - products 表
  - skus 表
    ↓
返回同步结果
```

## ⚠️ 常见问题

### 问题 1：ModuleNotFoundError: No module named 'celery'

**原因：** 依赖未安装或虚拟环境未激活

**解决方案：**
```bash
# 激活虚拟环境
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate     # Windows

# 安装依赖
pip install -r requirements.txt
```

### 问题 2：RabbitMQ 连接失败

**错误信息：**
```
kombu.exceptions.OperationalError: [Errno 111] Connection refused
```

**解决方案：**
```bash
# 检查 RabbitMQ 是否运行
docker ps | grep rabbitmq
# 或
sudo systemctl status rabbitmq-server

# 启动 RabbitMQ
sudo systemctl start rabbitmq-server
# 或
docker start rabbitmq

# 验证连接
python -c "from kombu import Connection; Connection('amqp://guest:guest@localhost:5672//').connect(); print('OK')"
```

### 问题 3：No client found for store_id

**原因：** `clients` 表中没有对应的 `store_id` 记录

**解决方案：**
1. 检查数据库中是否有该 store_id：
   ```sql
   SELECT * FROM clients WHERE store_id = 'STORE123';
   ```

2. 如果没有，创建客户端：
   ```bash
   curl -X POST "http://localhost:8000/api/client" \
     -H "x-admin-key: admin-api-key-change-in-production" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "My Store",
       "store_id": "STORE123",
       "store_key": "my-secret-key",
       "description": "My store"
     }'
   ```

### 问题 4：QPMN API 认证失败

**错误信息：**
```
HTTP error fetching products from QPMN: 401 Unauthorized
```

**原因：** `store_key` 不正确或无效

**解决方案：**
1. 检查 `clients` 表中的 `store_key` 是否正确：
   ```sql
   SELECT store_id, store_key FROM clients WHERE store_id = 'STORE123';
   ```

2. 更新正确的 `store_key`：
   ```bash
   curl -X PUT "http://localhost:8000/api/client/1" \
     -H "x-admin-key: admin-api-key-change-in-production" \
     -H "Content-Type: application/json" \
     -d '{
       "store_key": "correct-store-key"
     }'
   ```

### 问题 5：数据库连接失败

**错误信息：**
```
Can't connect to MySQL server
```

**解决方案：**
1. 检查 `.env` 文件中的数据库配置
2. 确保 MySQL 正在运行
3. 测试数据库连接：
   ```bash
   mysql -h localhost -u order_user -p order_gateway
   ```

## 📊 验证同步结果

### 检查产品数量

```sql
-- 查看某个店铺的产品数量
SELECT store_id, COUNT(*) as product_count 
FROM products 
WHERE store_id = 'STORE123'
GROUP BY store_id;
```

### 检查 SKU 数量

```sql
-- 查看某个店铺的 SKU 数量
SELECT p.store_id, COUNT(*) as sku_count 
FROM skus s
JOIN products p ON s.product_id = p.product_id
WHERE p.store_id = 'STORE123'
GROUP BY p.store_id;
```

### 查看最近同步的产品

```sql
SELECT product_id, product_code, description, created_at
FROM products
WHERE store_id = 'STORE123'
ORDER BY created_at DESC
LIMIT 10;
```

## 🔄 定时同步（可选）

如果需要定期自动同步，可以设置 cron job（Linux/Mac）：

```bash
# 编辑 crontab
crontab -e

# 添加以下行（每小时同步一次）
0 * * * * /path/to/order-gateway/venv/bin/python /path/to/order-gateway/sync_products.py -s STORE123 >> /var/log/product_sync.log 2>&1
```

Windows 可以使用任务计划程序。

## 🎯 最佳实践

1. **先测试再生产**：先在测试环境运行，确认无误后再在生产环境执行
2. **监控日志**：定期检查同步日志，确保没有错误
3. **备份数据**：在大批量同步前备份数据库
4. **验证 store_key**：确保 `clients` 表中的 `store_key` 是最新且有效的
5. **错误处理**：如果同步失败，查看完整错误信息进行排查

## 📝 注意事项

- ✅ `store_id` 是必需参数
- ✅ 必须先在 `clients` 表中配置 `store_key`
- ✅ RabbitMQ 必须正在运行
- ✅ 脚本会直接执行同步任务（不是异步的）
- ✅ 同步结果会立即显示在终端
- ❌ 不要在 Celery worker 运行时同时运行此脚本（可能导致冲突）

## 🔗 相关文档

- [Celery Worker 指南](CELERY_WORKER_GUIDE.md)
- [快速开始](QUICKSTART_CELERY.md)
- [API 文档](http://localhost:8000/docs)

---

**提示：** 如果遇到任何问题，请检查日志输出并参考故障排查部分。
