# 快速开始 - Celery Worker 和 QPMN 产品同步

## 5 分钟快速设置

### 步骤 1: 安装 RabbitMQ（使用 Docker）

```bash
docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3-management
```

验证 RabbitMQ 是否运行：
```bash
docker ps | grep rabbitmq
```

访问管理界面：http://localhost:15672 (用户名/密码: guest/guest)

### 步骤 2: 配置环境变量

编辑 `.env` 文件，设置 QPMN API 密钥：

```env
QPMN_API_URL=https://api.qpmn.com/v1
```

### 步骤 3: 安装依赖

```bash
pip install -r requirements.txt
```

### 步骤 4: 启动服务

**终端 1 - 启动 FastAPI 服务器：**
```bash
start.bat
```

**终端 2 - 启动 Celery Worker：**
```bash
start_celery_worker.bat
```

### 步骤 5: 测试同步

访问 Swagger UI：http://localhost:8000/docs

找到 **Product Sync** 部分，点击 `/api/sync/products`，然后点击 "Try it out" → "Execute"。

或者使用命令行：
```bash
curl -X POST "http://localhost:8000/api/sync/products" \
  -H "x-admin-key: admin-api-key-change-in-production"
```

## 常用命令

### 启动/停止服务

```bash
# 启动 FastAPI
start.bat

# 启动 Celery Worker
start_celery_worker.bat

# 停止 RabbitMQ
docker stop rabbitmq

# 启动 RabbitMQ
docker start rabbitmq
```

### 手动同步（测试用）

```bash
# 同步所有产品
python sync_products.py

# 同步特定店铺
python sync_products.py STORE123
```

### 查看任务状态

```bash
# 在 Swagger UI 中
GET /api/sync/status/{task_id}

# 或使用 curl
curl -X GET "http://localhost:8000/api/sync/status/{task_id}" \
  -H "x-admin-key: admin-api-key-change-in-production"
```

## 故障排查

### RabbitMQ 未运行
```bash
# 检查 RabbitMQ 状态
docker ps | grep rabbitmq

# 如果未运行，启动它
docker start rabbitmq
```

### Worker 未连接
```bash
# 重启 worker
# Ctrl+C 停止，然后重新运行 start_celery_worker.bat
```

### API 密钥错误
```bash
# 确认 ADMIN_API_KEY 与请求头中的值匹配
```

## 下一步

详细文档请查看：[CELERY_WORKER_GUIDE.md](CELERY_WORKER_GUIDE.md)
