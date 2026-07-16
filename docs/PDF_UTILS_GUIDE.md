# PyMuPDF PDF 处理工具指南

## 📖 概述

本项目已集成 PyMuPDF（fitz）库，提供强大的 PDF 文件处理能力，包括分割、合并、提取页面等功能。

## 📦 安装

### 1. 安装依赖

```bash
pip install PyMuPDF>=1.23.0
```

或更新所有依赖：
```bash
pip install -r requirements.txt
```

### 2. 验证安装

```python
import fitz
print(fitz.__version__)
```

## 🎯 功能特性

### 1. PDF 分割 (split_pdf)

将一个 PDF 文件分割成多个小文件。

```python
from app.services.pdf_utils import pdf_processor

# 将 PDF 按每页分割
output_files = pdf_processor.split_pdf(
    input_pdf_path="document.pdf",
    output_dir="./output",
    pages_per_file=1  # 每1页一个文件
)

# 将 PDF 按每2页分割
output_files = pdf_processor.split_pdf(
    input_pdf_path="document.pdf",
    output_dir="./output",
    pages_per_file=2  # 每2页一个文件
)

print(output_files)
# ['output/document_part_1.pdf', 'output/document_part_2.pdf', ...]
```

**参数：**
- `input_pdf_path`: 输入 PDF 文件路径
- `output_dir`: 输出目录
- `pages_per_file`: 每个文件的页数（默认 1）

**返回：**
- 分割后的文件路径列表

### 2. 提取指定页面 (extract_pages)

从 PDF 中提取特定页面并保存为新文件。

```python
from app.services.pdf_utils import pdf_processor

# 提取第 1、3、5 页
output_path = pdf_processor.extract_pages(
    input_pdf_path="document.pdf",
    page_numbers=[1, 3, 5],  # 1-indexed
    output_path="extracted.pdf"
)

print(f"Extracted to: {output_path}")
```

**参数：**
- `input_pdf_path`: 输入 PDF 文件路径
- `page_numbers`: 要提取的页码列表（从 1 开始）
- `output_path`: 输出文件路径

**返回：**
- 输出文件路径

### 3. 获取页数 (get_page_count)

获取 PDF 文件的总页数。

```python
from app.services.pdf_utils import pdf_processor

page_count = pdf_processor.get_page_count("document.pdf")
print(f"Total pages: {page_count}")
```

**参数：**
- `pdf_path`: PDF 文件路径

**返回：**
- 页数（整数）

### 4. 合并 PDF (merge_pdfs)

将多个 PDF 文件合并成一个。

```python
from app.services.pdf_utils import pdf_processor

pdf_files = [
    "part1.pdf",
    "part2.pdf",
    "part3.pdf"
]

output_path = pdf_processor.merge_pdfs(
    pdf_paths=pdf_files,
    output_path="merged.pdf"
)

print(f"Merged to: {output_path}")
```

**参数：**
- `pdf_paths`: PDF 文件路径列表
- `output_path`: 输出文件路径

**返回：**
- 合并后的文件路径

### 5. PDF 转图片 (pdf_to_images)

将 PDF 页面转换为图片（PNG 格式）。

```python
from app.services.pdf_utils import pdf_processor

# 转换为图片（300 DPI）
images = pdf_processor.pdf_to_images(
    pdf_path="document.pdf",
    dpi=300
)

# 保存图片
for i, img_data in enumerate(images):
    with open(f"page_{i+1}.png", "wb") as f:
        f.write(img_data)
```

**参数：**
- `pdf_path`: PDF 文件路径
- `dpi`: 分辨率（默认 300）

**返回：**
- 图片数据字节列表（PNG 格式）

### 6. 获取 PDF 信息 (get_pdf_info)

获取 PDF 文件的详细信息。

```python
from app.services.pdf_utils import pdf_processor

info = pdf_processor.get_pdf_info("document.pdf")
print(info)
# {
#     'page_count': 10,
#     'metadata': {...},
#     'is_encrypted': False
# }
```

**参数：**
- `pdf_path`: PDF 文件路径

**返回：**
- 包含 PDF 信息的字典

## 💡 实际应用场景

### 场景 1：订单文件分割

在打印订单中，可能需要将多页 PDF 分割成单页进行单独打印。

```python
from app.services.pdf_utils import pdf_processor
import tempfile
import os

def process_order_pdf(pdf_path: str, store_id: str) -> list:
    """
    Process order PDF by splitting into individual pages.
    
    Args:
        pdf_path: Path to the order PDF
        store_id: Store identifier for organization
        
    Returns:
        List of split file paths
    """
    # Create output directory
    output_dir = f"./uploads/{store_id}/split"
    os.makedirs(output_dir, exist_ok=True)
    
    # Split PDF into single pages
    split_files = pdf_processor.split_pdf(
        input_pdf_path=pdf_path,
        output_dir=output_dir,
        pages_per_file=1
    )
    
    return split_files

# Usage
split_files = process_order_pdf("order.pdf", "STORE123")
for file in split_files:
    print(f"Split file: {file}")
```

### 场景 2：提取特定页面

只提取订单中的特定页面（如封面、签名页）。

```python
from app.services.pdf_utils import pdf_processor

def extract_signature_pages(pdf_path: str, signature_pages: list) -> str:
    """
    Extract signature pages from a document.
    
    Args:
        pdf_path: Path to the document
        signature_pages: List of page numbers containing signatures
        
    Returns:
        Path to extracted PDF
    """
    output_path = f"signature_pages.pdf"
    
    extracted = pdf_processor.extract_pages(
        input_pdf_path=pdf_path,
        page_numbers=signature_pages,
        output_path=output_path
    )
    
    return extracted

# Extract pages 5 and 10 (signature pages)
sig_pdf = extract_signature_pages("contract.pdf", [5, 10])
```

### 场景 3：批量合并文档

将多个订单文档合并成一个批次文件。

```python
from app.services.pdf_utils import pdf_processor
import glob

def merge_batch_orders(batch_id: str) -> str:
    """
    Merge all order PDFs in a batch.
    
    Args:
        batch_id: Batch identifier
        
    Returns:
        Path to merged PDF
    """
    # Find all PDFs in batch directory
    pdf_pattern = f"./orders/batch_{batch_id}/*.pdf"
    pdf_files = sorted(glob.glob(pdf_pattern))
    
    if not pdf_files:
        raise ValueError(f"No PDFs found for batch {batch_id}")
    
    # Merge all PDFs
    output_path = f"./batches/batch_{batch_id}_merged.pdf"
    merged = pdf_processor.merge_pdfs(
        pdf_paths=pdf_files,
        output_path=output_path
    )
    
    return merged

# Merge batch 123
merged_pdf = merge_batch_orders("123")
```

### 场景 4：PDF 预览生成

生成 PDF 页面的缩略图用于预览。

```python
from app.services.pdf_utils import pdf_processor

def generate_thumbnails(pdf_path: str, thumbnail_dir: str, dpi: int = 72):
    """
    Generate thumbnail images for PDF pages.
    
    Args:
        pdf_path: Path to the PDF
        thumbnail_dir: Directory to save thumbnails
        dpi: Resolution for thumbnails (lower = smaller files)
    """
    import os
    os.makedirs(thumbnail_dir, exist_ok=True)
    
    # Convert to images at lower DPI for thumbnails
    images = pdf_processor.pdf_to_images(pdf_path, dpi=dpi)
    
    # Save thumbnails
    for i, img_data in enumerate(images):
        thumbnail_path = os.path.join(thumbnail_dir, f"page_{i+1}_thumb.png")
        with open(thumbnail_path, "wb") as f:
            f.write(img_data)
    
    return len(images)

# Generate thumbnails
count = generate_thumbnails("document.pdf", "./thumbnails", dpi=72)
print(f"Generated {count} thumbnails")
```

### 场景 5：PDF 验证和处理

在处理上传的 PDF 文件前进行验证。

```python
from app.services.pdf_utils import pdf_processor

def validate_and_process_pdf(pdf_path: str, max_pages: int = 100):
    """
    Validate PDF before processing.
    
    Args:
        pdf_path: Path to the PDF
        max_pages: Maximum allowed pages
        
    Returns:
        dict with validation results
    """
    try:
        # Get PDF info
        info = pdf_processor.get_pdf_info(pdf_path)
        
        # Check if encrypted
        if info['is_encrypted']:
            return {
                "valid": False,
                "error": "PDF is encrypted"
            }
        
        # Check page count
        if info['page_count'] > max_pages:
            return {
                "valid": False,
                "error": f"PDF has {info['page_count']} pages, maximum is {max_pages}"
            }
        
        return {
            "valid": True,
            "page_count": info['page_count'],
            "metadata": info['metadata']
        }
        
    except Exception as e:
        return {
            "valid": False,
            "error": str(e)
        }

# Validate uploaded PDF
result = validate_and_process_pdf("upload.pdf", max_pages=50)
if result['valid']:
    print(f"Valid PDF with {result['page_count']} pages")
else:
    print(f"Invalid: {result['error']}")
```

## 🔧 与 OSS 集成

结合阿里云 OSS 实现云端 PDF 处理：

```python
from app.services.pdf_utils import pdf_processor
from app.services.oss import oss_service
import tempfile
import os

def process_pdf_from_oss(object_key: str, store_id: str) -> list:
    """
    Download PDF from OSS, split it, and upload parts back to OSS.
    
    Args:
        object_key: OSS object key of the source PDF
        store_id: Store identifier
        
    Returns:
        List of OSS keys for split files
    """
    import requests
    
    # Get download URL from OSS
    download_url = oss_service.generate_download_url(object_key)
    
    # Download PDF to temporary file
    response = requests.get(download_url)
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(response.content)
        tmp_path = tmp.name
    
    try:
        # Split PDF
        output_dir = f"./temp/{store_id}"
        os.makedirs(output_dir, exist_ok=True)
        
        split_files = pdf_processor.split_pdf(
            input_pdf_path=tmp_path,
            output_dir=output_dir,
            pages_per_file=1
        )
        
        # Upload split files to OSS
        oss_keys = []
        for split_file in split_files:
            filename = os.path.basename(split_file)
            oss_key = f"{store_id}/split/{filename}"
            
            # Read file and upload (implementation depends on your OSS upload method)
            with open(split_file, 'rb') as f:
                # Upload to OSS here
                pass
            
            oss_keys.append(oss_key)
        
        return oss_keys
        
    finally:
        # Cleanup
        os.unlink(tmp_path)
        for split_file in split_files:
            if os.path.exists(split_file):
                os.unlink(split_file)

# Usage
oss_keys = process_pdf_from_oss("STORE123/document.pdf", "STORE123")
```

## ⚙️ 性能优化建议

### 1. 大文件处理

对于大 PDF 文件，考虑分批处理：

```python
def split_large_pdf(pdf_path: str, output_dir: str, batch_size: int = 10):
    """Split large PDF in batches to avoid memory issues."""
    page_count = pdf_processor.get_page_count(pdf_path)
    
    all_files = []
    for start in range(0, page_count, batch_size):
        end = min(start + batch_size, page_count)
        pages = list(range(start + 1, end + 1))
        
        batch_output = f"{output_dir}/batch_{start//batch_size}.pdf"
        pdf_processor.extract_pages(pdf_path, pages, batch_output)
        
        # Process batch
        batch_files = pdf_processor.split_pdf(batch_output, output_dir, 1)
        all_files.extend(batch_files)
        
        # Cleanup batch file
        os.unlink(batch_output)
    
    return all_files
```

### 2. 内存管理

处理完成后及时关闭文档：

```python
# Good practice
doc = fitz.open(pdf_path)
try:
    # Process PDF
    pass
finally:
    doc.close()  # Always close
```

### 3. 临时文件清理

使用上下文管理器确保清理：

```python
import tempfile
import os
from contextlib import contextmanager

@contextmanager
def temp_pdf():
    """Context manager for temporary PDF files."""
    tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    try:
        yield tmp.name
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
```

## 🐛 常见问题

### 问题 1：ModuleNotFoundError: No module named 'fitz'

**解决方案：**
```bash
pip install PyMuPDF
```

注意：包名是 `PyMuPDF`，但导入时使用 `fitz`。

### 问题 2：内存不足处理大文件

**解决方案：**
- 降低 DPI（图片转换时）
- 分批处理大文件
- 增加系统内存限制

### 问题 3：加密 PDF 无法处理

**解决方案：**
```python
doc = fitz.open(pdf_path)
if doc.is_encrypted:
    # Provide password
    doc.authenticate("password")
```

### 问题 4：字体缺失警告

**解决方案：**
这是正常警告，不影响功能。如需消除：
```python
import fitz
fitz.TOOLS.mupdf_display_errors(False)
```

## 📊 性能基准

典型性能参考（取决于文件大小和系统）：

| 操作 | 10页 PDF | 100页 PDF | 1000页 PDF |
|------|----------|-----------|------------|
| 分割（每页） | ~0.1s | ~1s | ~10s |
| 提取页面 | ~0.05s | ~0.5s | ~5s |
| 合并（10个文件） | ~0.2s | ~2s | ~20s |
| 转图片（300 DPI） | ~1s | ~10s | ~100s |

## 📚 相关资源

- [PyMuPDF 官方文档](https://pymupdf.readthedocs.io/)
- [PyMuPDF GitHub](https://github.com/pymupdf/PyMuPDF)
- [PDF 规范参考](https://www.adobe.com/content/dam/acom/en/devnet/pdf/pdfs/PDF32000_2008.pdf)

## ✨ 最佳实践

1. **始终关闭文档**：使用 try-finally 确保 doc.close() 被调用
2. **验证输入**：检查文件是否存在、是否有效 PDF
3. **错误处理**：捕获并妥善处理异常
4. **资源清理**：及时删除临时文件
5. **日志记录**：记录处理过程和错误
6. **权限检查**：确保有读写权限
7. **大小限制**：设置合理的文件大小和页数限制

---

**提示：** PyMuPDF 是一个非常强大的库，支持更多高级功能，如文本提取、注释处理、表单填写等。查看官方文档了解完整功能。
