#!/usr/bin/env python3
"""
download_images.py — 从清洗后的 Markdown 文件中提取 HTML <img> 标签，
                     下载图片到同目录下的 images/ 文件夹，并更新 md 文件中的引用路径。

处理流程：
  1. 解析 md 文件中所有 <img src="..."> 标签，提取图片 URL
  2. 在 md 文件所在目录下创建 images/ 文件夹
  3. 根据 URL 中的原始文件名（去除 query 参数后）命名图片，如有重名则加序号
  4. 下载图片，保存到 images/ 文件夹
  5. 将 md 文件中的 img src 替换为相对路径（./images/xxx.jpg）

用法：
    source venv/bin/activate
    python download_images.py 输入_clean.md        # 原地修改，图片保存到同目录 images/
    python download_images.py *.md                 # 批量处理

依赖：requests（pip install requests）、beautifulsoup4
"""

import sys
import os
import re
import hashlib
import urllib.parse
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    print("❌ 缺少依赖：requests，请运行 pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ 缺少依赖：beautifulsoup4，请运行 pip install beautifulsoup4", file=sys.stderr)
    sys.exit(1)


def extract_img_filename(url: str) -> str:
    """
    从 URL 中提取原始文件名（去除 query 参数）。
    如 https://xxx/imgs/img_in_chart_box_305_399_878_640.jpg?authorization=...
    → img_in_chart_box_305_399_878_640.jpg
    """
    parsed = urllib.parse.urlparse(url)
    # 取路径部分的最后一段
    path_part = parsed.path.rstrip("/")
    filename = os.path.basename(path_part)
    if not filename:
        # fallback：用 url 的 md5 前8位作为文件名
        ext = ".jpg"
        filename = hashlib.md5(url.encode()).hexdigest()[:8] + ext
    return filename


def get_unique_filename(images_dir: Path, base_name: str) -> str:
    """如果文件名已存在，追加序号：img.jpg → img_2.jpg → img_3.jpg ..."""
    target = images_dir / base_name
    if not target.exists():
        return base_name

    stem, suffix = os.path.splitext(base_name)
    counter = 2
    while True:
        new_name = f"{stem}_{counter}{suffix}"
        if not (images_dir / new_name).exists():
            return new_name
        counter += 1


def download_image(url: str, dest_path: Path, timeout: int = 30) -> bool:
    """下载单张图片，返回是否成功。"""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=timeout, stream=True)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except requests.RequestException as e:
        print(f"  ⚠️  下载失败：{url[:80]}...\n     原因：{e}", file=sys.stderr)
        return False


def process_md_file(md_path: str) -> None:
    """处理单个 md 文件：下载图片并替换 src 为本地相对路径。"""
    md_file = Path(md_path).resolve()
    if not md_file.exists():
        print(f"❌ 文件不存在：{md_path}", file=sys.stderr)
        return

    # 图片目录：与 md 文件同级的 images/ 文件夹
    images_dir = md_file.parent / "images"
    images_dir.mkdir(exist_ok=True)

    with open(md_file, "r", encoding="utf-8") as f:
        content = f.read()

    # 找到所有 <img src="..."> 的 URL（包含 HTML 属性中的双引号或单引号）
    # 匹配形如 src="..." 或 src='...'
    img_pattern = re.compile(r'<img\s[^>]*src=["\']([^"\']+)["\']', re.IGNORECASE)
    matches = list(img_pattern.finditer(content))

    if not matches:
        print(f"ℹ️  未发现图片：{md_path}")
        return

    print(f"\n📄 处理文件：{md_path}")
    print(f"   发现图片：{len(matches)} 张")

    # url → 本地文件名 的映射（去重：同一 URL 只下载一次）
    url_to_local: dict[str, str] = {}
    success_count = 0
    skip_count = 0

    for i, m in enumerate(matches, start=1):
        url = m.group(1)

        # 同一 URL 出现多次，跳过重复下载
        if url in url_to_local:
            print(f"  [{i}/{len(matches)}] 跳过（已下载）：{url_to_local[url]}")
            skip_count += 1
            continue

        base_name = extract_img_filename(url)
        local_name = get_unique_filename(images_dir, base_name)
        dest_path = images_dir / local_name

        if dest_path.exists():
            print(f"  [{i}/{len(matches)}] 已存在，跳过：{local_name}")
            url_to_local[url] = local_name
            skip_count += 1
            continue

        print(f"  [{i}/{len(matches)}] 下载中：{local_name}")
        ok = download_image(url, dest_path)
        if ok:
            url_to_local[url] = local_name
            success_count += 1
        else:
            # 下载失败，不替换原 src
            url_to_local[url] = None

    # 替换 md 内容中的 src
    def replace_src(m: re.Match) -> str:
        full_tag = m.group(0)
        url = m.group(1)
        local_name = url_to_local.get(url)
        if local_name is None:
            return full_tag  # 下载失败，保留原始 URL
        # 构建相对路径（相对于 md 文件所在目录）
        rel_path = f"./images/{local_name}"
        # 替换 src 的值
        new_tag = re.sub(
            r'(src=)["\']' + re.escape(url) + r'["\']',
            lambda mm: mm.group(1) + '"' + rel_path + '"',
            full_tag,
            count=1,
        )
        return new_tag

    new_content = img_pattern.sub(replace_src, content)

    # 写回文件
    with open(md_file, "w", encoding="utf-8") as f:
        f.write(new_content)

    total_downloaded = success_count
    total_skipped = skip_count
    failed_count = sum(1 for v in url_to_local.values() if v is None)

    print(f"\n✅ 完成：{md_path}")
    print(f"   ├─ 新下载：{total_downloaded} 张")
    print(f"   ├─ 已跳过：{total_skipped} 张")
    if failed_count:
        print(f"   └─ 失败：{failed_count} 张（src 保留原始 URL）")
    else:
        print(f"   └─ 图片保存至：{images_dir}")


def main():
    if len(sys.argv) < 2:
        print("用法：python download_images.py <文件.md> [文件2.md ...]")
        print("      python download_images.py *.md          # 批量处理")
        sys.exit(1)

    files = sys.argv[1:]
    for path in files:
        try:
            process_md_file(path)
        except Exception as e:
            print(f"❌ 处理 {path} 时出错：{e}", file=sys.stderr)


if __name__ == "__main__":
    main()
