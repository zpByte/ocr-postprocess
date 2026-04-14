# md-cleaner

将 OCR / 富文本 Markdown 清洗为标准 Markdown 的 Python 工具集，同时提供图片下载和 PDF 处理功能。

## 功能

| 脚本 | 说明 |
|------|------|
| `clean_md.py` | 将含 HTML 表格、内联样式、LaTeX 标注的富 Markdown 转为标准 pipe table Markdown |
| `download_images.py` | 从 Markdown 文件中提取 `<img>` URL，下载图片到本地 `images/` 目录并替换路径 |
| `pdf_tool.py` | PDF 合并、拆分、图片转 PDF |

## 环境要求

```bash
# 系统依赖（macOS）
brew install pandoc

# Python 依赖
pip install beautifulsoup4 requests pymupdf
```

建议使用虚拟环境：

```bash
python3 -m venv venv
source venv/bin/activate
pip install beautifulsoup4 requests pymupdf
```

## 使用方法

### clean_md.py — 清洗富 Markdown

将 PaddleOCR / Marker 等工具输出的含 HTML 表格的 Markdown 转换为标准 Markdown，方便上传到云文档。

```bash
# 单文件（生成 报告_clean.md）
python clean_md.py 报告.md

# 指定输出路径
python clean_md.py 报告.md 报告_output.md

# 批量处理
python clean_md.py *.md
```

**处理内容**：
- HTML `<table>` → Markdown pipe table（展开 colspan/rowspan）
- LaTeX 标注（`\underline`、`\uwave`、`\underset`）→ 加粗或纯文字
- 行首中文句号 `。`、实心圆点 `•` → Markdown 无序列表 `-`

### download_images.py — 下载 Markdown 内嵌图片

```bash
# 单文件（图片保存到同目录 images/，并替换 src 为相对路径）
python download_images.py 报告_clean.md

# 批量处理
python download_images.py *.md
```

### pdf_tool.py — PDF 工具

```bash
# 合并多个 PDF
python pdf_tool.py merge -i a.pdf b.pdf c.pdf -o merged.pdf

# 按页码范围拆分（页码从 1 开始）
python pdf_tool.py split -i input.pdf -r 1-3 5 7-9 -o output_dir/

# 每页拆分为单独 PDF
python pdf_tool.py split -i input.pdf -o output_dir/

# 将目录下所有图片合并为一个 PDF（按文件名自然排序）
python pdf_tool.py images2pdf -i ./images_dir -o output.pdf
```

## 典型工作流

```
原始 PDF / 图片
     ↓ （Marker / PaddleOCR 等工具识别，不含在此仓库）
富 Markdown（含 HTML 表格）
     ↓ clean_md.py
标准 Markdown（_clean.md）
     ↓ download_images.py（可选，如含在线图片）
本地图片 + 标准 Markdown
     ↓ 上传到云文档（飞书、Notion 等）
```
