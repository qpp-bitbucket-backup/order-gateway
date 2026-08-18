# HP PrintOS Site Flow API - 授权方式指南

## 📋 当前状态

**⚠️ 警告**: 当前 API **没有任何认证/授权保护**，所有端点都可以公开访问。

---

## 🔐 可用的授权方案

项目已准备了三种授权方式的基础设施：

### 方案一：API Key 认证 ⭐ 推荐用于服务器集成

**适用场景**: 
- 服务器对服务器通信
- HP PrintOS 集成
- Webhook 验证

**优点**:
- ✅ 简单易用
- ✅ 适合自动化流程
- ✅ 性能开销小

**实现文件**: `app/core/auth_api_key.py`

**使用方法**:

```python
from fastapi import Depends
from app.core.auth_api_key import get_api_key

@router.post("/order")
def submit_order(
    request: OrderSubmissionRequest,
    api_key: str = Depends(get_api_key)  # 添加这行
):
    # api_key 已验证通过
    pass
```

**客户端请求**:
```bash
curl -X POST http://localhost:8000/api/order \
  -H "X-API-Key: your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{...}'
```

**配置 API Keys** (`.env`):
```env
# 生产环境应从环境变量或密钥管理服务读取
API_KEYS=your-api-key-here,test-api-key
```

---

### 方案二：JWT Bearer Token 认证 👤 推荐用于用户认证

**适用场景**:
- 用户登录系统
- 多租户应用
- 需要细粒度权限控制

**优点**:
- ✅ 支持用户身份
- ✅ 可包含权限信息
- ✅ 有有效期限制

**实现文件**: `app/core/auth_jwt.py`

**使用方法**:

```python
from fastapi import Depends
from app.core.auth_jwt import get_current_user

@router.post("/order")
def submit_order(
    request: OrderSubmissionRequest,
    current_user: dict = Depends(get_current_user)  # 添加这行
):
    # current_user 包含解码的 JWT payload
    user_id = current_user.get("sub")
    pass
```

**客户端请求**:
```bash
curl -X POST http://localhost:8000/api/order \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
  -H "Content-Type: application/json" \
  -d '{...}'
```

**获取 Token**:
```python
from app.core.security import create_access_token

# 登录成功后创建 token
token = create_access_token(data={"sub": user_id, "role": "admin"})
```

---

### 方案三：HP PrintOS OAuth 2.0 🏭 生产环境标准

**适用场景**:
- 与 HP PrintOS 生态系统集成
- 需要符合 HP 安全标准
- 企业级部署

**实现步骤**:

1. **注册 HP Developer 账户**
   - 访问: https://developers.hp.com/printos

2. **创建应用程序**
   - 获取 Client ID 和 Client Secret

3. **实现 OAuth 2.0 流程**
   ```python
   # 示例：OAuth 2.0 客户端凭证流程
   import httpx
   
   async def get_printos_token():
       async with httpx.AsyncClient() as client:
           response = await client.post(
               "https://printos.api.hp.com/oauth/token",
               data={
                   "grant_type": "client_credentials",
                   "client_id": settings.HP_CLIENT_ID,
                   "client_secret": settings.HP_CLIENT_SECRET,
               }
           )
           return response.json()["access_token"]
   ```

4. **验证传入的 HP PrintOS 请求**
   ```python
   from app.core.auth_hp import verify_hp_signature
   
   @router.post("/webhook")
   def hp_webhook(
       request: Request,
       x_hp_signature: str = Header(...),
   ):
       verify_hp_signature(request.body(), x_hp_signature)
   ```

---

## 🚀 快速启用认证

### 选项 A: 为所有路由添加 API Key 认证

1. **更新 `.env`**:
   ```env
   API_KEY=your-secure-api-key-here-change-in-production
   ```

2. **创建认证依赖**:
   ```python
   # app/core/auth.py
   from fastapi import Depends, HTTPException, Header
   from app.core.config import settings
   
   def require_api_key(x_api_key: str = Header(...)):
       if x_api_key != settings.API_KEY:
           raise HTTPException(
               status_code=403,
               detail="Invalid API key"
           )
       return x_api_key
   ```

3. **应用到所有路由** (`app/main.py`):
   ```python
   from app.core.auth import require_api_key
   
   # 添加到所有路由器
   app.include_router(orders.router, dependencies=[Depends(require_api_key)])
   app.include_router(products.router, dependencies=[Depends(require_api_key)])
   app.include_router(files.router, dependencies=[Depends(require_api_key)])
   ```

### 选项 B: 选择性认证（推荐）

只为敏感端点添加认证：

```python
# app/api/orders.py
from app.core.auth_api_key import get_api_key

@router.post("/order")
def submit_order(
    request: OrderSubmissionRequest,
    api_key: str = Depends(get_api_key)  # 需要认证
):
    pass

@router.get("/product")
def get_products():
    # 公开访问，无需认证
    pass
```

---

## 📊 授权方案对比

| 特性 | API Key | JWT Token | OAuth 2.0 |
|------|---------|-----------|-----------|
| 实现难度 | ⭐ 简单 | ⭐⭐ 中等 | ⭐⭐⭐ 复杂 |
| 安全性 | ⭐⭐ 中 | ⭐⭐⭐ 高 | ⭐⭐⭐⭐ 最高 |
| 适用场景 | 服务器集成 | 用户系统 | 企业集成 |
| 性能 | ⭐⭐⭐ 快 | ⭐⭐ 中 | ⭐ 较慢 |
| 令牌管理 | 手动 | 自动过期 | 自动刷新 |
| HP PrintOS 兼容 | ✅ | ❌ | ✅✅ |

---

## 🔧 生产环境建议

### 最小安全配置

1. **启用 API Key 认证**
2. **使用 HTTPS**
3. **设置 CORS 限制**
4. **速率限制**
5. **日志记录**

### 完整安全配置

1. **HP PrintOS OAuth 2.0**
2. **JWT Token 用于内部用户**
3. **API Key 用于 webhook**
4. **HTTPS + TLS 1.3**
5. **WAF (Web Application Firewall)**
6. **IP 白名单**
7. **审计日志**

---

## 📝 下一步行动

### 立即可做

1. **选择授权方案**（推荐 API Key 开始）
2. **更新 `.env` 添加密钥**
3. **在路由中添加 `Depends(get_api_key)`**
4. **测试认证是否工作**

### 中期改进

1. **实现数据库存储的 API Keys**
2. **添加速率限制**
3. **设置监控和告警**

### 长期规划

1. **迁移到 HP PrintOS OAuth 2.0**
2. **实现细粒度权限控制**
3. **添加审计日志系统**

---

## 🆘 需要帮助？

如果要实现特定的授权方案，请告诉我，我可以帮您：
- 完整实现选定的认证系统
- 创建登录/注册端点
- 集成 HP PrintOS OAuth
- 添加权限管理系统
- 编写安全测试用例
