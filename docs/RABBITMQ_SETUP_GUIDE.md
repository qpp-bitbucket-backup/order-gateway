# RabbitMQ Celery 配置指南

## 📖 概述

本项目已将 Celery 消息队列从 Redis 迁移到 **RabbitMQ**。RabbitMQ 提供了更可靠的消息传递和更好的企业级特性。

## 🔄 变更内容

### 1. 依赖变更

**之前（Redis）：**
```txt
redis>=5.0.0
```

**现在（RabbitMQ）：**
```txt
amqp>=5.2.0
```

### 2. 配置变更

**之前（Redis）：**
```python
CELERY_BROKER_URL = "redis://localhost:6379/0"
CELERY_RESULT_BACKEND = "redis://localhost:6379/0"
```

**现在（RabbitMQ）：**
```python
CELERY_BROKER_URL = "amqp://guest:guest@localhost:5672//"
CELERY_RESULT_BACKEND = "rpc://"
```

## 🚀 安装和启动 RabbitMQ

### Windows 环境

#### 方法 1：使用 Docker（推荐）

```bash
# 拉取 RabbitMQ 镜像（带管理界面）
docker pull rabbitmq:3-management

# 启动 RabbitMQ 容器
docker run -d ^
  --name rabbitmq ^
  -p 5672:5672 ^
  -p 15672:15672 ^
  rabbitmq:3-management

# 验证是否运行
docker ps | findstr rabbitmq
```

**访问管理界面：**
- URL: http://localhost:15672
- 用户名: `guest`
- 密码: `guest`

#### 方法 2：直接安装

1. 下载并安装 Erlang: https://www.erlang.org/downloads
2. 下载并安装 RabbitMQ: https://www.rabbitmq.com/install-windows.html
3. 启动 RabbitMQ 服务：

```bash
# 启用管理插件
rabbitmq-plugins enable rabbitmq_management

# 启动服务
net start RabbitMQ
```

### Ubuntu/Linux 环境

#### 方法 1：使用 Docker（推荐）

```bash
# 拉取并运行 RabbitMQ
docker run -d \
  --name rabbitmq \
  -p 5672:5672 \
  -p 15672:15672 \
  rabbitmq:3-management

# 验证
docker ps | grep rabbitmq
```

#### 方法 2：系统安装

```bash
# 更新包列表
sudo apt-get update

# 安装 Erlang 和 RabbitMQ
sudo apt-get install -y erlang rabbitmq-server

# 启动服务
sudo systemctl start rabbitmq-server
sudo systemctl enable rabbitmq-server

# 启用管理插件
sudo rabbitmq-plugins enable rabbitmq_management

# 检查状态
sudo systemctl status rabbitmq-server
```

## ⚙️ 配置说明

### 环境变量配置

编辑 `.env` 文件：

```env
# Celery Configuration (RabbitMQ)
CELERY_BROKER_URL=amqp://guest:guest@localhost:5672//
CELERY_RESULT_BACKEND=rpc://
```

### 自定义 RabbitMQ 凭据

如果使用非默认的 RabbitMQ 配置：

```env
# 格式: amqp://username:password@host:port/vhost
CELERY_BROKER_URL=amqp://myuser:mypassword@rabbitmq.example.com:5672/myvhost
CELERY_RESULT_BACKEND=rpc://
```

### Docker Compose 配置

如果使用 Docker Compose，可以添加 RabbitMQ 服务：

```yaml
version: '3.8'

services:
  rabbitmq:
    image: rabbitmq:3-management
    container_name: rabbitmq
    ports:
      - "5672:5672"    # AMQP 协议端口
      - "15672:15672"  # 管理界面端口
    environment:
      - RABBITMQ_DEFAULT_USER=guest
      - RABBITMQ_DEFAULT_PASS=guest
    volumes:
      - rabbitmq_data:/var/lib/rabbitmq
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "-q", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  app:
    build: .
    depends_on:
      - rabbitmq
      - mysql
    environment:
      - CELERY_BROKER_URL=amqp://guest:guest@rabbitmq:5672//
      - CELERY_RESULT_BACKEND=rpc://

volumes:
  rabbitmq_data:
```

## 🔧 启动 Celery Worker

### Windows

```bash
# 激活虚拟环境（如果使用）
venv\Scripts\activate

# 启动 Celery worker
celery -A app.core.celery worker --loglevel=info --pool=solo
```

### Linux/Ubuntu

```bash
# 激活虚拟环境（如果使用）
source venv/bin/activate

# 启动 Celery worker
celery -A app.core.celery worker --loglevel=info
```

### 后台运行（生产环境）

```bash
# 使用 nohup
nohup celery -A app.core.celery worker --loglevel=info > celery.log 2>&1 &

# 或使用 systemd（创建 /etc/systemd/system/celery.service）
sudo systemctl start celery
sudo systemctl enable celery
```

## ✅ 验证安装

### 1. 检查 RabbitMQ 连接

```python
# 测试脚本
python -c "
from kombu import Connection
conn = Connection('amqp://guest:guest@localhost:5672//')
try:
    conn.ensure_connection(max_retries=3)
    print('✅ RabbitMQ 连接成功')
except Exception as e:
    print(f'❌ 连接失败: {e}')
finally:
    conn.release()
"
```

### 2. 测试 Celery 任务

```python
# 测试 Celery 配置
python -c "
from app.core.celery import celery_app
print(f'Celery broker: {celery_app.conf.broker_url}')
print(f'Celery backend: {celery_app.conf.result_backend}')

# 发送测试任务
from app.core.celery import debug_task
result = debug_task.delay()
print(f'Task ID: {result.id}')
print('✅ Celery 配置正确')
"
```

### 3. 查看 RabbitMQ 管理界面

1. 打开浏览器访问: http://localhost:15672
2. 登录: `guest` / `guest`
3. 查看 Queues、Exchanges、Connections 等

## 📊 监控和管理

### RabbitMQ 命令行工具

```bash
# 查看所有队列
rabbitmqctl list_queues

# 查看连接
rabbitmqctl list_connections

# 查看消费者
rabbitmqctl list_consumers

# 查看节点状态
rabbitmqctl status

# 清除所有队列数据（谨慎使用）
rabbitmqctl reset
```

### Python 监控

```python
from app.core.celery import celery_app

# 检查 worker 状态
inspect = celery_app.control.inspect()
active_workers = inspect.active()
print(f"Active workers: {active_workers}")

# 检查队列长度
from kombu import Connection
conn = Connection(celery_app.conf.broker_url)
channel = conn.channel()
queue_declare = channel.queue_declare(queue='celery', passive=True)
message_count = queue_declare.message_count
print(f"Messages in queue: {message_count}")
```

## 🔐 安全配置

### 生产环境最佳实践

1. **修改默认密码**

```bash
# 删除默认 guest 用户
rabbitmqctl delete_user guest

# 创建新用户
rabbitmqctl add_user admin strong_password
rabbitmqctl set_user_tags admin administrator
rabbitmqctl set_permissions -p / admin ".*" ".*" ".*"
```

2. **配置环境变量**

```env
CELERY_BROKER_URL=amqp://admin:strong_password@rabbitmq.prod.com:5672//
```

3. **启用 SSL/TLS**

```env
CELERY_BROKER_URL=amqps://admin:strong_password@rabbitmq.prod.com:5671//
```

4. **限制网络访问**

```bash
# 防火墙规则（仅允许应用服务器访问）
ufw allow from 192.168.1.0/24 to any port 5672
```

## 🐛 故障排查

### 问题 1：连接被拒绝

```
Error: [Errno 111] Connection refused
```

**解决方案：**
```bash
# 检查 RabbitMQ 是否运行
docker ps | grep rabbitmq
# 或
sudo systemctl status rabbitmq-server

# 检查端口是否监听
netstat -an | grep 5672
# 或
ss -tlnp | grep 5672

# 启动 RabbitMQ
docker start rabbitmq
# 或
sudo systemctl start rabbitmq-server
```

### 问题 2：认证失败

```
AccessRefused: Login failed
```

**解决方案：**
```bash
# 检查用户名和密码
rabbitmqctl list_users

# 重置密码
rabbitmqctl change_password guest new_password

# 检查权限
rabbitmqctl list_permissions -p /
```

### 问题 3：Celery Worker 无法启动

```
kombu.exceptions.OperationalError: ...
```

**解决方案：**
```bash
# 1. 验证 RabbitMQ 连接
python -c "from kombu import Connection; Connection('amqp://guest:guest@localhost:5672//').connect()"

# 2. 检查 Celery 配置
python -c "from app.core.celery import celery_app; print(celery_app.conf.broker_url)"

# 3. 查看详细日志
celery -A app.core.celery worker --loglevel=debug
```

### 问题 4：消息堆积

```bash
# 查看队列长度
rabbitmqctl list_queues name messages

# 清理队列（谨慎使用）
rabbitmqctl purge_queue celery

# 增加 worker 数量
celery -A app.core.celery worker --loglevel=info --concurrency=4
```

## 📈 性能优化

### 1. 调整 Worker 并发数

```bash
# 根据 CPU 核心数设置
celery -A app.core.celery worker --concurrency=4
```

### 2. 预取优化

在 `app/core/celery.py` 中调整：

```python
celery_app.conf.update(
    worker_prefetch_multiplier=1,  # 每次只预取一个任务
    # ...
)
```

### 3. 消息持久化

确保任务配置了持久化：

```python
@celery_app.task(bind=True, name="tasks.products.sync_products")
def sync_products(self):
    # 任务实现
    pass

# 发送时指定持久化
sync_products.apply_async(expires=3600)
```

## 🎯 与 Redis 的对比

| 特性 | RabbitMQ | Redis |
|------|----------|-------|
| 可靠性 | ⭐⭐⭐⭐⭐ 高 | ⭐⭐⭐ 中等 |
| 性能 | ⭐⭐⭐⭐ 好 | ⭐⭐⭐⭐⭐ 优秀 |
| 复杂度 | 较高 | 简单 |
| 内存占用 | 较高 | 较低 |
| 消息持久化 | ✅ 原生支持 | ⚠️ 需配置 |
| 管理界面 | ✅ 内置 | ❌ 需第三方 |
| 适用场景 | 企业级、关键任务 | 缓存、简单队列 |

## 📚 相关资源

- [RabbitMQ 官方文档](https://www.rabbitmq.com/documentation.html)
- [Celery + RabbitMQ 配置](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/rabbitmq.html)
- [Kombu 文档](https://kombu.readthedocs.io/)

## ✅ 完成清单

- [ ] 安装 RabbitMQ
- [ ] 启动 RabbitMQ 服务
- [ ] 更新 `.env` 配置文件
- [ ] 安装新依赖: `pip install -r requirements.txt`
- [ ] 验证 RabbitMQ 连接
- [ ] 启动 Celery worker
- [ ] 测试任务执行
- [ ] 检查管理界面

---

**迁移完成！** 现在你的项目使用 RabbitMQ 作为 Celery 的消息代理。🎉
