#!/usr/bin/env python3
"""
merge_pages.py — 多张图片（同一份报告的各页）→ 按文件名顺序合并为一个 Markdown

用法：
    source venv/bin/activate
    python merge_pages.py 输出文件名.md 页1.png 页2.png 页3.png ...

    也可以用 glob 传入一个目录下所有图片（按文件名自然排序）：
    python merge_pages.py 报告.md 报告目录/*.png

每张图单独 OCR 识别，识别结果原样拼接，不做任何 AI 分析或内容合并。
页与页之间用分隔线 + 页码标注。
"""
import sys
import os
import re

DISABLE_CHECK = "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"
os.environ.setdefault(DISABLE_CHECK, "True")

from paddleocr import PPStructureV3


def natural_sort_key(s: str):
    """自然排序 key，让 page2.png 排在 page10.png 之前。"""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", s)]


def merge_pages(output_path: str, image_paths: list[str]) -> str:
    """按顺序 OCR 多张图片，合并为一个 Markdown 文件。"""
    if not image_paths:
        raise ValueError("未提供任何图片路径")

    # 按自然顺序排序
    image_paths = sorted(image_paths, key=natural_sort_key)

    supported = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"}
    for p in image_paths:
        if not os.path.exists(p):
            raise FileNotFoundError(f"文件不存在：{p}")
        ext = os.path.splitext(p)[1].lower()
        if ext not in supported:
            raise ValueError(f"不支持的格式：{ext}（仅支持图片，PDF 请用 convert.py）")

    pipeline = PPStructureV3(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
    )

    all_pages = []
    total = len(image_paths)

    for i, img_path in enumerate(image_paths, start=1):
        print(f"  [{i}/{total}] 处理：{os.path.basename(img_path)}")
        results = pipeline.predict(input=img_path)

        page_parts = []
        for res in results:
            md = res.get("markdown", "")
            if md:
                page_parts.append(md.strip())

        page_content = "\n\n".join(page_parts) if page_parts else "（本页无识别内容）"
        all_pages.append((i, os.path.basename(img_path), page_content))

    # 拼接：每页前加页码标注
    sections = []
    for page_num, filename, content in all_pages:
        header = f"<!-- Page {page_num}: {filename} -->"
        sections.append(f"{header}\n\n{content}")

    final_content = "\n\n---\n\n".join(sections)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(final_content)

    print(f"\n✅ 合并完成（共 {total} 页）：{output_path}")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法：python merge_pages.py <输出.md> <图片1> [图片2 ...]")
        print("示例：python merge_pages.py 竞对报告.md 报告_p1.png 报告_p2.png")
        sys.exit(1)

    output = sys.argv[1]
    images = sys.argv[2:]

    try:
        merge_pages(output, images)
    except Exception as e:
        print(f"❌ 错误：{e}", file=sys.stderr)
        sys.exit(1)
