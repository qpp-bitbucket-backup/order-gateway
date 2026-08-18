# Alembic 迁移故障排除指南

## 常见错误及解决方案

### 错误 1: `NameError: name 'sqlmodel' is not defined`

**错误信息**:
```
File "C:\apps\order-gateway\alembic\versions\xxx_migration.py", line XX, in upgrade
    sa.Column('file_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
                         ^^^^^^^^
NameError: name 'sqlmodel' is not defined
```

**原因**: 
Alembic 自动生成的迁移脚本使用了 `sqlmodel.sql.sqltypes.AutoString()`，但忘记导入 `sqlmodel` 模块。

**解决方案**:
在迁移文件的顶部添加导入：

```python
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # <-- 添加这一行
```

### 错误 2: 无法连接到 MySQL

**错误信息**:
```
pymysql.err.OperationalError: (2003, "Can't connect to MySQL server on 'localhost'")
```

**解决方案**:
1. 检查 MySQL 是否运行：
   ```bash
   docker-compose ps
   ```

2. 启动 MySQL（如果使用 Docker）：
   ```bash
   docker-compose up -d mysql
   ```

3. 等待 MySQL 就绪（约 10-20 秒）

### 错误 3: 访问被拒绝

**错误信息**:
```
Access denied for user 'order_user'@'localhost'
```

**解决方案**:
1. 检查 `.env` 文件中的凭据是否正确
2. 验证 MySQL 用户是否存在：
   ```sql
   SELECT User, Host FROM mysql.user;
   ```

3. 重新创建用户：
   ```sql
   DROP USER IF EXISTS 'order_user'@'localhost';
   CREATE USER 'order_user'@'localhost' IDENTIFIED BY 'order_password';
   GRANT ALL PRIVILEGES ON order_gateway.* TO 'order_user'@'localhost';
   FLUSH PRIVILEGES;
   ```

### 错误 4: 数据库不存在

**错误信息**:
```
Unknown database 'order_gateway'
```

**解决方案**:
```sql
CREATE DATABASE IF NOT EXISTS order_gateway 
    CHARACTER SET utf8mb4 
    COLLATE utf8mb4_unicode_ci;
```

或使用 Docker Compose 自动创建：
```bash
docker-compose up -d mysql
```

### 错误 5: 迁移已经是最新版本

**信息**:
```
INFO  [alembic.runtime.migration] Context impl MySQLImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade xxx -> yyy, description
```

**说明**: 这不是错误，表示迁移已成功应用。

## 常用修复命令

### 重置迁移历史（谨慎使用！）

```bash
# 删除所有迁移记录（不删除表）
python -m alembic stamp base

# 重新应用所有迁移
python -m alembic upgrade head
```

### 回退到特定版本

```bash
# 回退一个版本
python -m alembic downgrade -1

# 回退到基础版本
python -m alembic downgrade base

# 回退到特定版本
python -m alembic downgrade <revision_id>
```

### 生成新的迁移

```bash
# 自动检测模型变化
python -m alembic revision --autogenerate -m "描述变化"

# 手动创建空迁移
python -m alembic revision -m "描述变化"
```

### 查看迁移状态

```bash
# 查看当前版本
python -m alembic current

# 查看迁移历史
python -m alembic history --verbose

# 查看待处理的迁移
python -m alembic heads
```

## 最佳实践

### 1. 修改自动生成的迁移脚本

Alembic 的 `--autogenerate` 功能可能不完美，总是检查并手动修正：

```python
# 确保导入了 sqlmodel
import sqlmodel

# 检查列类型是否正确
sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False)

# 检查外键约束
sa.ForeignKeyConstraint(['user_id'], ['users.id'])

# 检查索引
op.create_index('ix_table_column', 'table', ['column'])
```

### 2. 测试迁移

在应用迁移前：
1. 在开发环境测试
2. 检查 `upgrade()` 和 `downgrade()` 都正确
3. 备份生产数据库

### 3. 编写有意义的迁移消息

```bash
# 好
python -m alembic revision --autogenerate -m "Add user_email column to users table"

# 不好
python -m alembic revision --autogenerate -m "update"
```

### 4. 保持迁移原子性

每个迁移应该只做一件事：
- 添加/删除表
- 添加/删除列
- 修改数据类型

不要在一个迁移中做多个不相关的变化。

## 手动创建迁移示例

### 添加新表

```python
def upgrade() -> None:
    op.create_table('new_table',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_new_table_name', 'new_table', ['name'], unique=True)

def downgrade() -> None:
    op.drop_index('ix_new_table_name', table_name='new_table')
    op.drop_table('new_table')
```

### 添加列

```python
def upgrade() -> None:
    op.add_column('orders', 
        sa.Column('new_field', sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )

def downgrade() -> None:
    op.drop_column('orders', 'new_field')
```

### 修改列类型

```python
def upgrade() -> None:
    op.alter_column('orders', 'total_amount',
        existing_type=sa.Float(),
        type_=sa.DECIMAL(10, 2),
        existing_nullable=False
    )

def downgrade() -> None:
    op.alter_column('orders', 'total_amount',
        existing_type=sa.DECIMAL(10, 2),
        type_=sa.Float(),
        existing_nullable=False
    )
```

### 添加外键

```python
def upgrade() -> None:
    op.create_foreign_key('fk_orders_customer_id',
        'orders', 'customers',
        ['customer_id'], ['id']
    )

def downgrade() -> None:
    op.drop_constraint('fk_orders_customer_id', 'orders')
```

## 快速参考

| 命令 | 用途 |
|------|------|
| `alembic upgrade head` | 应用到最新版本 |
| `alembic downgrade -1` | 回退一个版本 |
| `alembic current` | 查看当前版本 |
| `alembic history` | 查看历史 |
| `alembic revision --autogenerate -m "msg"` | 生成新迁移 |
| `alembic stamp base` | 重置迁移历史 |

## 需要帮助？

如果遇到问题：
1. 检查 Alembic 日志中的详细错误信息
2. 查看 `alembic/versions/` 中的迁移文件
3. 确认数据库连接正常
4. 查阅 [Alembic 官方文档](https://alembic.sqlalchemy.org/)
