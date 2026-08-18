# 阿里云 OSS 文件上传集成指南

## 📖 概述

本项目已集成阿里云对象存储服务（OSS），用于处理文件上传和下载。`/api/file/getpreupload` 端点现在返回阿里云 OSS 的预签名上传和下载链接。

## 🏗️ 架构

```
客户端请求
    ↓
FastAPI (/api/file/getpreupload)
    ↓
生成唯一 file_id
    ↓
创建 OSS object_key: {store_id}/{file_id}.{ext}
    ↓
调用 OSS SDK 生成预签名 URL
    ├─ Upload URL (PUT, 1小时有效)
    └─ Download URL (GET, 24小时有效)
    ↓
保存到 file_uploads 表
    ↓
返回 URLs 给客户端
```

## ⚙️ 配置

### 1. 安装依赖

```bash
pip install oss2>=2.18.0
```

或更新所有依赖：
```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

编辑 `.env` 文件，添加阿里云 OSS 配置：

```env
# Alibaba Cloud OSS Configuration
OSS_ACCESS_KEY_ID=LTAI5t...your-access-key-id
OSS_ACCESS_KEY_SECRET=your-access-key-secret
OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
OSS_BUCKET_NAME=my-order-gateway-bucket
OSS_REGION=cn-hangzhou
OSS_UPLOAD_EXPIRY_SECONDS=3600
OSS_DOWNLOAD_EXPIRY_SECONDS=86400
```

### 3. 获取阿里云 OSS 凭证

1. 登录 [阿里云控制台](https://oss.console.aliyun.com/)
2. 创建 OSS Bucket
3. 创建 AccessKey：
   - 进入 RAM 访问控制
   - 创建用户并授予 OSS 权限
   - 生成 AccessKey ID 和 Secret

### 4. 配置 Bucket 权限

推荐的 Bucket 配置：
- **读写权限**：私有（private）
- **跨域设置**：允许你的域名
- **版本控制**：可选启用

## 🎯 API 使用

### GET /api/file/getpreupload

获取文件上传的预签名 URL。

**请求参数：**
- `mime_type` (query, required): 文件的 MIME 类型

**认证：**
- 需要 OneFlow 签名认证
- 自动关联客户端的 store_id

**响应：**
```json
{
  "upload": "https://bucket.oss-cn-hangzhou.aliyuncs.com/store123/file.pdf?OSSAccessKeyId=...&Expires=...&Signature=...",
  "fetch": "https://bucket.oss-cn-hangzhou.aliyuncs.com/store123/file.pdf?OSSAccessKeyId=...&Expires=...&Signature=..."
}
```

### 使用示例

#### 1. 获取上传 URL

```bash
curl -X GET "http://localhost:8000/api/file/getpreupload?mime_type=application/pdf" \
  -H "x-oneflow-authorization: YOUR_TOKEN:SIGNATURE" \
  -H "x-oneflow-date: 2026-06-23T12:00:00.000Z" \
  -H "x-oneflow-algorithm: SHA256"
```

响应：
```json
{
  "upload": "https://my-bucket.oss-cn-hangzhou.aliyuncs.com/STORE123/abc123.pdf?OSSAccessKeyId=LTAI...&Expires=1719129600&Signature=xyz...",
  "fetch": "https://my-bucket.oss-cn-hangzhou.aliyuncs.com/STORE123/abc123.pdf?OSSAccessKeyId=LTAI...&Expires=1719216000&Signature=abc..."
}
```

#### 2. 上传文件到 OSS

使用返回的 `upload` URL 上传文件（PUT 请求）：

```bash
curl -X PUT "https://my-bucket.oss-cn-hangzhou.aliyuncs.com/STORE123/abc123.pdf?OSSAccessKeyId=...&Expires=...&Signature=..." \
  -H "Content-Type: application/pdf" \
  --data-binary @document.pdf
```

**注意：**
- 必须使用 PUT 方法
- 必须设置正确的 Content-Type
- URL 中包含签名，有效期 1 小时

#### 3. 在订单中使用文件

使用 `fetch` URL 在订单 JSON 中引用文件：

```json
{
  "destination": {
    "name": "MyStore"
  },
  "orderData": {
    "sourceOrderId": "ORDER-123",
    "items": [
      {
        "sku": "BUSINESS_CARD",
        "components": [
          {
            "code": "front",
            "fetch": true,
            "path": "https://my-bucket.oss-cn-hangzhou.aliyuncs.com/STORE123/abc123.pdf?OSSAccessKeyId=...&Expires=...&Signature=..."
          }
        ]
      }
    ]
  }
}
```

## 📁 文件组织结构

文件在 OSS 中的存储结构：

```
bucket-name/
├── STORE123/
│   ├── file-id-1.pdf
│   ├── file-id-2.jpg
│   └── file-id-3.png
├── STORE456/
│   ├── file-id-4.pdf
│   └── file-id-5.jpg
└── uploads/          # Bootstrap credentials 上传的文件
    └── file-id-6.pdf
```

**优点：**
- 按 store_id 组织，便于管理
- 支持多租户隔离
- 易于清理和统计

## 🔐 安全特性

### 1. 预签名 URL

- **Upload URL**: 
  - 有效期：1 小时（可配置）
  - 仅限 PUT 操作
  - 绑定 Content-Type
  
- **Download URL**:
  - 有效期：24 小时（可配置）
  - 仅限 GET 操作
  - 可随时重新生成

### 2. 访问控制

- Bucket 设置为私有
- 只有通过签名 URL 才能访问
- 签名包含过期时间，自动失效

### 3. Store ID 隔离

- 每个客户端的文件存储在各自的 store_id 目录下
- 无法访问其他店铺的文件
- 数据库记录包含 store_id 用于审计

## 💡 支持的 MIME 类型

系统自动根据 MIME 类型设置文件扩展名：

| MIME Type | Extension |
|-----------|-----------|
| application/pdf | .pdf |
| image/jpeg | .jpg |
| image/png | .png |
| image/gif | .gif |
| text/plain | .txt |
| 其他 | .bin |

## ⚙️ 配置选项

### OSS_UPLOAD_EXPIRY_SECONDS

上传 URL 的有效期（秒）。

```env
OSS_UPLOAD_EXPIRY_SECONDS=3600  # 1 小时
```

**建议：**
- 开发环境：3600（1 小时）
- 生产环境：1800（30 分钟）

### OSS_DOWNLOAD_EXPIRY_SECONDS

下载 URL 的有效期（秒）。

```env
OSS_DOWNLOAD_EXPIRY_SECONDS=86400  # 24 小时
```

**建议：**
- 短期使用：3600（1 小时）
- 长期使用：86400（24 小时）
- 订单引用：604800（7 天）

## 🛠️ OSS 服务类

位置：`app/services/oss.py`

### 主要方法

#### generate_upload_url(object_key, mime_type)

生成上传文件的预签名 URL。

```python
from app.services.oss import oss_service

upload_url = oss_service.generate_upload_url(
    "STORE123/file.pdf",
    "application/pdf"
)
```

#### generate_download_url(object_key)

生成下载文件的预签名 URL。

```python
fetch_url = oss_service.generate_download_url("STORE123/file.pdf")
```

#### get_object_url(object_key)

获取对象的公共 URL（如果 bucket 是公开的）。

```python
public_url = oss_service.get_object_url("STORE123/file.pdf")
# 返回: https://bucket.oss-cn-hangzhou.aliyuncs.com/STORE123/file.pdf
```

## 📊 数据库模型

FileUpload 表结构：

```sql
CREATE TABLE file_uploads (
    id INT AUTO_INCREMENT PRIMARY KEY,
    file_id VARCHAR(255) UNIQUE NOT NULL,
    mime_type VARCHAR(255) NOT NULL,
    upload_url TEXT NOT NULL,
    fetch_url TEXT NOT NULL,
    uploaded_at DATETIME,
    file_size INT,
    status VARCHAR(50) DEFAULT 'pending',
    store_id VARCHAR(255),
    created_at DATETIME,
    updated_at DATETIME,
    INDEX idx_store_id (store_id),
    INDEX idx_file_id (file_id)
);
```

## 🔍 监控和管理

### 查询文件上传记录

```sql
-- 查看某个店铺的文件
SELECT * FROM file_uploads 
WHERE store_id = 'STORE123' 
ORDER BY created_at DESC;

-- 查看待上传的文件
SELECT * FROM file_uploads 
WHERE status = 'pending';

-- 统计文件大小
SELECT store_id, COUNT(*) as file_count, SUM(file_size) as total_size
FROM file_uploads
GROUP BY store_id;
```

### OSS 控制台

在阿里云 OSS 控制台可以：
- 查看文件列表
- 监控流量和请求
- 设置生命周期规则
- 查看访问日志

## ⚠️ 注意事项

### 1. URL 过期

预签名 URL 会过期，如果过期需要重新调用 API 获取新的 URL。

**错误响应：**
```xml
<Error>
  <Code>AccessDenied</Code>
  <Message>Request has expired.</Message>
</Error>
```

**解决方案：**
- 重新调用 `/api/file/getpreupload` 获取新 URL
- 调整 `OSS_UPLOAD_EXPIRY_SECONDS` 配置

### 2. Content-Type 匹配

上传时必须使用正确的 Content-Type，否则 OSS 会拒绝请求。

```bash
# 正确
curl -X PUT "UPLOAD_URL" \
  -H "Content-Type: application/pdf" \
  --data-binary @file.pdf

# 错误 - 缺少 Content-Type
curl -X PUT "UPLOAD_URL" \
  --data-binary @file.pdf
```

### 3. 文件大小限制

OSS 单个文件最大 5GB。对于大文件，建议使用分片上传。

### 4. 费用

OSS 按以下项目收费：
- 存储空间（GB/月）
- 流量（GB）
- 请求次数

**优化建议：**
- 设置生命周期规则自动删除旧文件
- 使用 CDN 加速下载
- 压缩文件减少存储空间

## 🐛 故障排查

### 问题 1：AccessKeyId 不存在

**错误：**
```
The OSS Access Key Id you provided does not exist in our records.
```

**解决方案：**
- 检查 `OSS_ACCESS_KEY_ID` 是否正确
- 确认 AccessKey 已激活
- 检查是否使用了正确的区域 endpoint

### 问题 2：签名不匹配

**错误：**
```
The request signature we calculated does not match the signature you provided.
```

**解决方案：**
- 检查 `OSS_ACCESS_KEY_SECRET` 是否正确
- 确认没有多余的空格或换行符
- 检查系统时间是否同步

### 问题 3：Bucket 不存在

**错误：**
```
The specified bucket does not exist.
```

**解决方案：**
- 检查 `OSS_BUCKET_NAME` 是否正确
- 确认 Bucket 在指定的区域创建
- 检查 `OSS_ENDPOINT` 是否正确

### 问题 4：权限不足

**错误：**
```
AccessDenied: You do not have write access to this bucket.
```

**解决方案：**
- 检查 RAM 用户的 OSS 权限
- 确认 Bucket 策略允许写入
- 检查 ACL 设置

## 📚 相关资源

- [阿里云 OSS 官方文档](https://help.aliyun.com/product/31815.html)
- [oss2 Python SDK 文档](https://aliyun-oss-python-sdk.readthedocs.io/)
- [OSS API 参考](https://help.aliyun.com/document_detail/31947.html)

## ✨ 最佳实践

1. **定期清理**：设置生命周期规则自动删除超过 30 天的文件
2. **监控用量**：定期检查 OSS 使用量和费用
3. **备份重要文件**：关键文件应有备份策略
4. **使用 CDN**：频繁访问的文件通过 CDN 加速
5. **限制文件大小**：在应用层验证文件大小
6. **记录审计日志**：记录所有文件上传操作

---

**提示：** 如有问题，请检查 OSS 配置和 RAM 权限设置。
