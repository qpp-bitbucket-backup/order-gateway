# 快速开始 - MySQL 迁移

## 最简单的三步（使用 Docker）

### 1️⃣ 运行设置脚本

双击运行：
```
setup_mysql.bat
```

或在命令行：
```bash
cd c:\apps\order-gateway
setup_mysql.bat
```

这将自动：
- ✅ 启动 MySQL 容器
- ✅ 创建数据库和用户
- ✅ 运行数据库迁移

### 2️⃣ 启动应用

```bash
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8001
```

### 3️⃣ 访问 API 文档

打开浏览器：http://localhost:8001/docs

---

## 手动步骤（如果不想用脚本）

### 选项 A: Docker Compose

```bash
# 1. 启动 MySQL
docker-compose up -d mysql

# 2. 等待 15 秒让 MySQL 启动
timeout /t 15

# 3. 运行迁移
python -m alembic upgrade head

# 4. 启动应用
python -m uvicorn app.main:app --reload --port 8001
```

### 选项 B: 本地 MySQL Server

1. 安装 MySQL Server
2. 创建数据库：
   ```sql
   CREATE DATABASE order_gateway;
   CREATE USER 'order_user'@'localhost' IDENTIFIED BY 'order_password';
   GRANT ALL ON order_gateway.* TO 'order_user'@'localhost';
   ```
3. 更新 `.env` 文件中的 `DATABASE_URL`
4. 运行迁移：
   ```bash
   python -m alembic upgrade head
   ```

---

## 常用命令

```bash
# 查看迁移状态
python -m alembic current

# 生成新迁移
python -m alembic revision --autogenerate -m "描述"

# 应用迁移
python -m alembic upgrade head

# 回退迁移
python -m alembic downgrade -1

# 停止 MySQL
docker-compose down

# 停止并删除数据（谨慎使用！）
docker-compose down -v
```

---

## 连接信息

| 项目 | 值 |
|------|-----|
| 主机 | localhost |
| 端口 | 3306 |
| 数据库 | order_gateway |
| 用户 | order_user |
| 密码 | order_password |

---

## 详细文档

完整指南请查看：[MYSQL_MIGRATION_GUIDE.md](MYSQL_MIGRATION_GUIDE.md)
