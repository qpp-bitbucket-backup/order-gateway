# MySQL 数据库迁移完整指南

## 前置要求

在开始之前，您需要安装以下软件之一：

### 选项 1: Docker Desktop（推荐 - 最简单）
- 下载并安装 [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)

### 选项 2: 本地 MySQL Server
- 下载并安装 [MySQL Community Server](https://dev.mysql.com/downloads/mysql/)
- 或安装 [XAMPP](https://www.apachefriends.org/) (包含 MySQL)

---

## 方法一：使用 Docker Compose（推荐）

这是最简单的方法，无需手动安装 MySQL。

### 步骤 1: 启动 MySQL 容器

```bash
cd c:\apps\order-gateway
docker-compose up -d mysql
```

这将启动一个 MySQL 8.0 容器，自动创建：
- 数据库：`order_gateway`
- 用户：`order_user`
- 密码：`order_password`

### 步骤 2: 等待 MySQL 就绪

```bash
# 检查 MySQL 是否已启动
docker-compose ps

# 查看日志
docker-compose logs mysql
```

等待看到类似这样的消息：
```
[Server] /usr/sbin/mysqld: ready for connections.
```

### 步骤 3: 运行数据库迁移

```bash
# 生成迁移脚本
python -m alembic revision --autogenerate -m "Initial migration"

# 应用迁移
python -m alembic upgrade head
```

### 步骤 4: 启动应用

```bash
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8001
```

---

## 方法二：手动安装 MySQL Server

### 步骤 1: 安装 MySQL

1. 下载 MySQL Installer from https://dev.mysql.com/downloads/installer/
2. 运行安装程序，选择 "Developer Default"
3. 设置 root 密码（请记住这个密码）
4. 完成安装

### 步骤 2: 创建数据库和用户

打开 MySQL Command Line Client 或 MySQL Workbench，执行：

```sql
-- 创建数据库
CREATE DATABASE IF NOT EXISTS order_gateway 
    CHARACTER SET utf8mb4 
    COLLATE utf8mb4_unicode_ci;

-- 创建用户并授权
CREATE USER IF NOT EXISTS 'order_user'@'localhost' 
    IDENTIFIED BY 'order_password';

GRANT ALL PRIVILEGES ON order_gateway.* TO 'order_user'@'localhost';

FLUSH PRIVILEGES;
```

### 步骤 3: 更新 .env 文件

编辑 `c:\apps\order-gateway\.env`：

```env
# 如果使用默认端口 3306
DATABASE_URL=mysql+pymysql://order_user:order_password@localhost:3306/order_gateway

# 如果修改了密码，请相应更新
```

### 步骤 4: 运行迁移

```bash
cd c:\apps\order-gateway

# 初始化 Alembic（如果还没有）
python -m alembic init alembic

# 生成迁移脚本
python -m alembic revision --autogenerate -m "Initial migration"

# 应用迁移
python -m alembic upgrade head
```

---

## 方法三：从 SQLite 迁移到 MySQL

如果您已经有 SQLite 数据需要迁移：

### 步骤 1: 导出 SQLite 数据

```python
# create_migration_script.py
import sqlite3
import json
from datetime import datetime,timezone

# Connect to SQLite
sqlite_conn = sqlite3.connect('order_gateway.db')
sqlite_cursor = sqlite_conn.cursor()

# Export data
tables = ['orders', 'products', 'skus', 'file_uploads']
exported_data = {}

for table in tables:
    try:
        sqlite_cursor.execute(f"SELECT * FROM {table}")
        columns = [description[0] for description in sqlite_cursor.description]
        rows = sqlite_cursor.fetchall()
        
        exported_data[table] = {
            'columns': columns,
            'data': [dict(zip(columns, row)) for row in rows]
        }
    except Exception as e:
        print(f"Error exporting {table}: {e}")

# Save to JSON
with open('sqlite_export.json', 'w', encoding='utf-8') as f:
    json.dump(exported_data, f, indent=2, default=str)

print("Data exported to sqlite_export.json")
sqlite_conn.close()
```

### 步骤 2: 导入到 MySQL

```python
# import_to_mysql.py
import json
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

load_dotenv()

# Connect to MySQL
engine = create_engine(os.getenv('DATABASE_URL'), echo=False)

# Load exported data
with open('sqlite_export.json', 'r', encoding='utf-8') as f:
    exported_data = json.load(f)

# Import data
with engine.connect() as conn:
    for table_name, table_data in exported_data.items():
        if not table_data['data']:
            continue
            
        print(f"Importing {len(table_data['data'])} records into {table_name}...")
        
        for record in table_data['data']:
            # Remove auto-increment ID
            if 'id' in record:
                del record['id']
            
            # Convert datetime strings back to datetime
            for key, value in record.items():
                if isinstance(value, str):
                    try:
                        record[key] = datetime.fromisoformat(value)
                    except:
                        pass
            
            # Build INSERT statement
            columns = ', '.join(record.keys())
            placeholders = ', '.join([f':{key}' for key in record.keys()])
            
            stmt = text(f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})")
            conn.execute(stmt, record)
    
    conn.commit()

print("Import completed!")
```

---

## Alembic 迁移命令参考

### 基本命令

```bash
# 生成新的迁移脚本
python -m alembic revision --autogenerate -m "描述性消息"

# 应用所有待处理的迁移
python -m alembic upgrade head

# 回退一个版本
python -m alembic downgrade -1

# 查看当前版本
python -m alembic current

# 查看迁移历史
python -m alembic history

# 生成空迁移脚本（手动编写）
python -m alembic revision -m "描述性消息"
```

### 常见迁移操作示例

#### 添加新字段

```python
# 在 alembic/versions/xxx_add_field.py
def upgrade():
    op.add_column('orders', sa.Column('new_field', sa.String(100), nullable=True))

def downgrade():
    op.drop_column('orders', 'new_field')
```

#### 修改字段类型

```python
def upgrade():
    op.alter_column('orders', 'total_amount',
                    existing_type=sa.Float(),
                    type_=sa.DECIMAL(10, 2))

def downgrade():
    op.alter_column('orders', 'total_amount',
                    existing_type=sa.DECIMAL(10, 2),
                    type_=sa.Float())
```

#### 添加索引

```python
def upgrade():
    op.create_index('ix_orders_status', 'orders', ['status'])

def downgrade():
    op.drop_index('ix_orders_status', 'orders')
```

---

## 故障排除

### 问题 1: 无法连接到 MySQL

**错误**: `Can't connect to MySQL server on 'localhost'`

**解决方案**:
```bash
# 检查 MySQL 是否运行
docker-compose ps

# 或检查服务状态（Windows）
net start | findstr MySQL

# 检查端口是否被占用
netstat -ano | findstr :3306
```

### 问题 2: 访问被拒绝

**错误**: `Access denied for user 'order_user'@'localhost'`

**解决方案**:
```sql
-- 重新创建用户
DROP USER IF EXISTS 'order_user'@'localhost';
CREATE USER 'order_user'@'localhost' IDENTIFIED BY 'order_password';
GRANT ALL PRIVILEGES ON order_gateway.* TO 'order_user'@'localhost';
FLUSH PRIVILEGES;
```

### 问题 3: 字符集问题

**解决方案**: 确保数据库使用 utf8mb4：
```sql
ALTER DATABASE order_gateway 
    CHARACTER SET utf8mb4 
    COLLATE utf8mb4_unicode_ci;
```

### 问题 4: Alembic 未检测到表变化

**解决方案**:
1. 确保 `alembic/env.py` 中导入了所有模型
2. 确保 `target_metadata = BaseModel.metadata` 设置正确
3. 手动编辑生成的迁移脚本

---

## 验证迁移成功

### 方法 1: 检查数据库表

```sql
USE order_gateway;
SHOW TABLES;

-- 应该看到:
-- file_uploads
-- orders
-- products
-- skus
```

### 方法 2: 运行测试

```bash
cd c:\apps\order-gateway
python test_app.py
```

### 方法 3: 通过 API 验证

```bash
# 健康检查
curl http://localhost:8001/health

# 创建测试订单
curl -X POST http://localhost:8001/api/order \
  -H "Content-Type: application/json" \
  -d '{
    "destination": {"name": "test"},
    "orderData": {
      "sourceOrderId": "TEST-001",
      "items": [{"sku": "TEST-SKU"}]
    }
  }'
```

---

## 生产环境建议

### 1. 环境变量配置

```env
# .env.production
DATABASE_URL=mysql+pymysql://user:strong-password@mysql-host:3306/order_gateway
DEBUG=false
SECRET_KEY=your-super-secret-key-here
```

### 2. Docker Compose 生产配置

```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  mysql:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: order_gateway
      MYSQL_USER: ${MYSQL_USER}
      MYSQL_PASSWORD: ${MYSQL_PASSWORD}
    volumes:
      - mysql_data:/var/lib/mysql
    restart: always

  app:
    build: .
    environment:
      DATABASE_URL: mysql+pymysql://${MYSQL_USER}:${MYSQL_PASSWORD}@mysql:3306/order_gateway
    depends_on:
      - mysql
    restart: always

volumes:
  mysql_data:
```

### 3. 定期备份

```bash
# 备份数据库
docker-compose exec mysql mysqldump -u order_user -p order_gateway > backup.sql

# 恢复数据库
docker-compose exec -T mysql mysql -u order_user -p order_gateway < backup.sql
```

---

## 快速开始（一键启动）

```bash
# 1. 进入项目目录
cd c:\apps\order-gateway

# 2. 启动 MySQL（Docker）
docker-compose up -d mysql

# 3. 等待 MySQL 就绪（约 10-20 秒）
timeout /t 15

# 4. 运行迁移
python -m alembic upgrade head

# 5. 启动应用
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8001

# 6. 打开浏览器访问
start http://localhost:8001/docs
```

---

## 总结

✅ **已完成配置**:
- Alembic 迁移系统已初始化
- env.py 已配置好所有模型
- .env 文件已更新为 MySQL 连接字符串

📋 **下一步**:
1. 启动 MySQL（使用 Docker 或本地安装）
2. 运行 `python -m alembic upgrade head`
3. 启动应用并测试

💡 **推荐**: 使用 Docker Compose 方法，最简单且与生产环境一致！
