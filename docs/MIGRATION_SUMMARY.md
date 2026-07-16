# Redis 到 RabbitMQ 迁移总结

## 📋 概述

本项目已成功将 Celery 消息队列从 **Redis** 迁移到 **RabbitMQ**。RabbitMQ 提供了更可靠的消息传递、更好的企业级特性和内置的管理界面。

## ✅ 已完成的更改

### 1. 依赖更新

**文件：** [requirements.txt](file:///c:/apps/order-gateway/requirements.txt)

```diff
- redis>=5.0.0
+ amqp>=5.2.0
```

**说明：**
- 移除了 `redis` 包
- 添加了 `amqp` 包（Celery 与 RabbitMQ 通信所需）
- `celery>=5.3.0` 保持不变（已包含 RabbitMQ 支持）

### 2. 配置更新

#### app/core/config.py

**文件：** [app/core/config.py](file:///c:/apps/order-gateway/app/core/config.py)

```python
# 之前（Redis）
CELERY_BROKER_URL: str = "redis://localhost:6379/0"
CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"

# 现在（RabbitMQ）
CELERY_BROKER_URL: str = "amqp://guest:guest@localhost:5672//"
CELERY_RESULT_BACKEND: str = "rpc://"
```

#### .env 和 .env.example

**文件：** [.env](file:///c:/apps/order-gateway/.env), [.env.example](file:///c:/apps/order-gateway/.env.example)

```env
# Celery Configuration (RabbitMQ)
CELERY_BROKER_URL=amqp://guest:guest@localhost:5672//
CELERY_RESULT_BACKEND=rpc://
```

### 3. 文档更新

以下文档已全部更新，将 Redis 引用替换为 RabbitMQ：

- ✅ [RABBITMQ_SETUP_GUIDE.md](file:///c:/apps/order-gateway/RABBITMQ_SETUP_GUIDE.md) - **新建**完整的 RabbitMQ 配置指南
- ✅ [CELERY_WORKER_GUIDE.md](file:///c:/apps/order-gateway/CELERY_WORKER_GUIDE.md) - 更新了架构图和安装说明
- ✅ [QUICKSTART_CELERY.md](file:///c:/apps/order-gateway/QUICKSTART_CELERY.md) - 更新了快速开始步骤
- ✅ [SYNC_PRODUCTS_USAGE.md](file:///c:/apps/order-gateway/SYNC_PRODUCTS_USAGE.md) - 更新了前置要求和故障排查

## 🔧 如何使用

### 第一步：启动 RabbitMQ

#### Windows（使用 Docker）

```bash
docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3-management
```

#### Linux/Ubuntu

```bash
# 方法 1: Docker
docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3-management

# 方法 2: 系统安装
sudo apt-get install -y erlang rabbitmq-server
sudo systemctl start rabbitmq-server
sudo rabbitmq-plugins enable rabbitmq_management
```

### 第二步：验证连接

```bash
# 检查 RabbitMQ 是否运行
docker ps | grep rabbitmq

# 测试 Python 连接
python -c "from kombu import Connection; Connection('amqp://guest:guest@localhost:5672//').connect(); print('✅ 连接成功')"
```

### 第三步：启动 Celery Worker

```bash
# Windows
start_celery_worker.bat

# Linux
celery -A app.core.celery worker --loglevel=info
```

### 第四步：访问管理界面

打开浏览器访问：**http://localhost:15672**

- 用户名：`guest`
- 密码：`guest`

你可以在此查看：
- Queues（队列）
- Exchanges（交换机）
- Connections（连接）
- Channels（通道）

## 📊 配置对比

| 配置项 | Redis | RabbitMQ |
|--------|-------|----------|
| Broker URL | `redis://localhost:6379/0` | `amqp://guest:guest@localhost:5672//` |
| Result Backend | `redis://localhost:6379/0` | `rpc://` |
| 默认端口 | 6379 | 5672 (AMQP), 15672 (管理) |
| 管理界面 | ❌ 需第三方工具 | ✅ 内置 Web UI |
| 依赖包 | `redis>=5.0.0` | `amqp>=5.2.0` |

## 🎯 优势

### RabbitMQ 的优势

1. **更高的可靠性**
   - 原生支持消息持久化
   - 确认机制确保消息不丢失
   - 支持事务

2. **更好的企业级特性**
   - 复杂的路由规则
   - 消息优先级
   - 死信队列
   - TTL（消息存活时间）

3. **内置管理界面**
   - 实时监控队列状态
   - 可视化的消息流
   - 用户和权限管理
   - 插件系统

4. **更好的扩展性**
   - 集群支持
   - Federation（联邦）
   - Shovel（铲子插件）

### 性能对比

| 场景 | Redis | RabbitMQ |
|------|-------|----------|
| 简单任务队列 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| 高吞吐量 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| 消息可靠性 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 复杂路由 | ⭐⭐ | ⭐⭐⭐⭐⭐ |
| 持久化 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

## 🔄 迁移影响

### 无需更改的代码

✅ **以下代码无需修改：**
- Celery 任务定义（`app/tasks/*.py`）
- 任务调用方式（`.delay()`, `.apply_async()`）
- FastAPI 集成
- 数据库模型

### 需要更新的配置

⚠️ **以下配置需要更新：**
- `.env` 文件中的 `CELERY_BROKER_URL`
- `.env` 文件中的 `CELERY_RESULT_BACKEND`
- Docker Compose 配置（如果使用）
- CI/CD 配置文件
- 部署脚本

### 运维变更

🔧 **运维方面的变化：**
- 安装和维护 RabbitMQ 而非 Redis
- 使用 RabbitMQ 管理界面监控
- 不同的备份和恢复策略
- 不同的日志位置

## 🐛 常见问题

### Q1: 为什么要从 Redis 迁移到 RabbitMQ？

**A:** 
- 更高的消息可靠性
- 更好的企业级功能
- 内置的管理和监控工具
- 更适合生产环境的复杂场景

### Q2: 迁移后性能会下降吗？

**A:** 
对于大多数应用场景，性能差异可以忽略不计。RabbitMQ 在消息可靠性方面做得更好，而 Redis 在极端高吞吐场景下略有优势。

### Q3: 我可以同时使用 Redis 和 RabbitMQ 吗？

**A:** 
可以，但不推荐用于同一个 Celery 实例。如果你需要 Redis 作为缓存，可以同时运行两者，但 Celery 只能使用一个 broker。

### Q4: 如何回滚到 Redis？

**A:** 
如果需要回滚：
1. 修改 `.env` 中的配置为 Redis URL
2. 修改 `requirements.txt`，换回 `redis` 包
3. 运行 `pip install -r requirements.txt`
4. 重启 Celery worker

### Q5: RabbitMQ 的资源占用更高吗？

**A:** 
是的，RabbitMQ 比 Redis 占用更多内存和 CPU。但对于中等规模的应用，这种差异通常可以接受。

## 📚 相关资源

- [RabbitMQ 官方文档](https://www.rabbitmq.com/documentation.html)
- [Celery + RabbitMQ 指南](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/rabbitmq.html)
- [Kombu 文档](https://kombu.readthedocs.io/)
- [项目 RabbitMQ 配置指南](RABBITMQ_SETUP_GUIDE.md)

## ✅ 验证清单

完成以下步骤确保迁移成功：

- [ ] RabbitMQ 正在运行
- [ ] 可以访问管理界面 (http://localhost:15672)
- [ ] Python 可以连接到 RabbitMQ
- [ ] Celery worker 成功启动
- [ ] 可以发送和接收任务
- [ ] 产品同步任务正常工作
- [ ] 没有 Redis 相关的错误日志

## 🎉 总结

迁移已完成！你的项目现在使用 RabbitMQ 作为 Celery 的消息代理，享受更可靠的消息传递和更好的管理功能。

如有问题，请参考 [RABBITMQ_SETUP_GUIDE.md](RABBITMQ_SETUP_GUIDE.md) 获取详细的配置和故障排查指南。
