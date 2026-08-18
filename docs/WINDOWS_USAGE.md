# Windows 使用指南 - HP PrintOS Order Gateway API

## 解决端口冲突错误 [WinError 10013]

如果您看到错误：`[WinError 10013] An attempt was made to access a socket in a way forbidden by its access permissions`

这表示端口已被占用。以下是解决方案：

### 方案一：使用自动检测端口的启动脚本（推荐）

双击运行：
```
c:\apps\order-gateway\start.bat
```

该脚本会自动检测端口 8000 是否被占用，如果被占用则使用端口 8001。

### 方案二：手动指定其他端口

```bash
cd c:\apps\order-gateway
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 方案三：查找并停止占用端口的进程

```bash
# 1. 查找占用端口 8000 的进程
netstat -ano | findstr :8000

# 2. 记下最后一列的 PID（进程ID）
# 例如：TCP  0.0.0.0:8000  0.0.0.0:0  LISTENING  12345

# 3. 在任务管理器中结束该进程，或使用命令：
taskkill /PID <PID号码>
```

### 方案四：在 PowerShell 中查找和停止进程

```powershell
# 查找占用端口的进程
Get-NetTCPConnection -LocalPort 8000 | Select-Object OwningProcess

# 停止进程
Stop-Process -Id <PID号码>
```

## 快速启动方式

### 方法 1：批处理文件（最简单）
```
双击: start.bat
```

### 方法 2：PowerShell 脚本
```powershell
cd c:\apps\order-gateway
.\start.ps1
```

### 方法 3：命令行
```bash
cd c:\apps\order-gateway
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8001
```

### 方法 4：Docker Desktop
```bash
cd c:\apps\order-gateway
docker-compose up -d
```

## 当前运行状态

服务器当前正在 **端口 8001** 上运行：

- **API 文档**: http://localhost:8001/docs
- **ReDoc 文档**: http://localhost:8001/redoc
- **健康检查**: http://localhost:8001/health
- **OpenAPI JSON**: http://localhost:8001/openapi.json

## 测试 API

打开新的命令行窗口运行：

```bash
cd c:\apps\order-gateway
python test_app.py
```

注意：如果服务器运行在非 8000 端口，需要先修改 `test_app.py` 中的 `base_url`。

## 常用端口

| 端口 | 用途 | 说明 |
|------|------|------|
| 8000 | 默认端口 | 首选端口 |
| 8001 | 备用端口 | 当 8000 被占用时使用 |
| 8002 | 备用端口 | 当 8000 和 8001 都被占用时使用 |

## 常见问题

### Q: 如何查看哪些端口被占用？
```bash
netstat -ano | findstr "LISTENING"
```

### Q: 如何永久更改默认端口？
编辑 `.env` 文件，添加或修改：
```
PORT=8001
```

### Q: Docker 运行时端口冲突怎么办？
编辑 `docker-compose.yml`，修改端口映射：
```yaml
ports:
  - "8001:8000"  # 将宿主机的 8001 映射到容器的 8000
```

### Q: 如何在后台运行服务器？
```bash
# Windows 可以使用 start 命令
start python -m uvicorn app.main:app --host 0.0.0.0 --port 8001

# 或者使用 PowerShell Start-Job
Start-Job -ScriptBlock { python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 }
```

## 停止服务器

- **命令行运行**: 按 `Ctrl+C`
- **批处理文件**: 关闭窗口或按任意键（如果有 pause）
- **Docker**: `docker-compose down`
