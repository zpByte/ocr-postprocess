#!/usr/bin/env python3
"""
PDF 拆分 & 合并工具（基于 PyMuPDF）

用法示例：
  # 合并多个 PDF
  python pdf_tool.py merge -i a.pdf b.pdf c.pdf -o merged.pdf

  # 按页码范围拆分（页码从 1 开始）
  python pdf_tool.py split -i input.pdf -r 1-3 5 7-9 -o output_dir

  # 每页拆分为单独 PDF
  python pdf_tool.py split -i input.pdf -o output_dir

  # 将目录下所有图片合并为一个 PDF（按文件名自然排序）
  python pdf_tool.py images2pdf -i ./images_dir -o output.pdf
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF


# ─── 合并 ────────────────────────────────────────────────────────────────────

def merge_pdfs(input_files: list[str], output_file: str) -> None:
    """将多个 PDF 合并为一个 PDF。"""
    merged = fitz.open()
    for path in input_files:
        p = Path(path)
        if not p.exists():
            print(f"[错误] 文件不存在：{path}")
            sys.exit(1)
        with fitz.open(str(p)) as doc:
            merged.insert_pdf(doc)
        print(f"  已添加：{path}（{len(fitz.open(str(p)))} 页）")

    merged.save(output_file)
    print(f"\n✅ 合并完成 → {output_file}（共 {len(merged)} 页）")


# ─── 拆分 ────────────────────────────────────────────────────────────────────

def parse_ranges(range_strs: list[str], total_pages: int) -> list[list[int]]:
    """
    解析页码范围字符串，返回 0-indexed 页码列表的列表。
    例如 ["1-3", "5", "7-9"] → [[0,1,2], [4], [6,7,8]]
    """
    groups = []
    for r in range_strs:
        r = r.strip()
        if "-" in r:
            start_s, end_s = r.split("-", 1)
            start, end = int(start_s), int(end_s)
            if start < 1 or end > total_pages or start > end:
                print(f"[错误] 无效范围：{r}（文档共 {total_pages} 页）")
                sys.exit(1)
            groups.append(list(range(start - 1, end)))
        else:
            page = int(r)
            if page < 1 or page > total_pages:
                print(f"[错误] 页码超出范围：{r}（文档共 {total_pages} 页）")
                sys.exit(1)
            groups.append([page - 1])
    return groups


def split_pdf(input_file: str, output_dir: str, ranges: Optional[list] = None) -> None:
    """
    拆分 PDF。
    - ranges 为 None 时：每页单独保存。
    - ranges 不为空时：按指定范围拆分，每个范围保存为一个文件。
    """
    src_path = Path(input_file)
    if not src_path.exists():
        print(f"[错误] 文件不存在：{input_file}")
        sys.exit(1)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    import tempfile, os as _os
    # 先保存一份清理过的临时文件，规避部分 PDF 的对象引用问题
    _tmp = tempfile.mktemp(suffix=".pdf")
    with fitz.open(str(src_path)) as _raw:
        _raw.save(_tmp, garbage=4, deflate=True)

    try:
        with fitz.open(_tmp) as doc:
            total = len(doc)
            stem = src_path.stem

            if not ranges:
                # 每页拆分
                for i in range(total):
                    new_doc = fitz.open()
                    new_doc.insert_pdf(doc, from_page=i, to_page=i)
                    out_path = out_dir / f"{stem}_p{i + 1:04d}.pdf"
                    new_doc.save(str(out_path))
                    new_doc.close()
                    print(f"  → {out_path}")
                print(f"\n✅ 拆分完成：共生成 {total} 个文件，保存于 {out_dir}/")
            else:
                groups = parse_ranges(ranges, total)
                for idx, pages in enumerate(groups, start=1):
                    new_doc = fitz.open()
                    # 连续范围一次性插入，非连续逐页插入（源已是清理后文件，无引用问题）
                    if pages == list(range(pages[0], pages[-1] + 1)):
                        new_doc.insert_pdf(doc, from_page=pages[0], to_page=pages[-1])
                    else:
                        for page_num in pages:
                            new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
                    if len(pages) == 1:
                        label = f"p{pages[0] + 1}"
                    else:
                        label = f"p{pages[0] + 1}-p{pages[-1] + 1}"
                    out_path = out_dir / f"{stem}_{label}.pdf"
                    new_doc.save(str(out_path))
                    new_doc.close()
                    print(f"  → {out_path}（页：{[p + 1 for p in pages]}）")
                print(f"\n✅ 拆分完成：共生成 {len(groups)} 个文件，保存于 {out_dir}/")
    finally:
        _os.unlink(_tmp)


# ─── 图片转 PDF ───────────────────────────────────────────────────────────────

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".tif", ".webp"}

def _natural_key(path: Path):
    """自然排序 key，使 1.jpg < 2.jpg < 10.jpg。"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def images_to_pdf(input_dir: str, output_file: str) -> None:
    """
    将目录下所有图片按文件名自然排序合并为一个 PDF。
    每张图片占一页，页面大小等于图片原始分辨率。
    """
    src_dir = Path(input_dir)
    if not src_dir.is_dir():
        print(f"[错误] 目录不存在：{input_dir}")
        sys.exit(1)

    images = sorted(
        [p for p in src_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS],
        key=_natural_key,
    )

    if not images:
        print(f"[错误] 目录中未找到图片文件：{input_dir}")
        sys.exit(1)

    out_doc = fitz.open()
    for img_path in images:
        # 打开图片，获取尺寸
        img_doc = fitz.open(str(img_path))
        # fitz 可以直接 open 常见图片格式，生成单页 PDF
        pdfbytes = img_doc.convert_to_pdf()
        img_doc.close()

        img_pdf = fitz.open("pdf", pdfbytes)
        out_doc.insert_pdf(img_pdf)
        img_pdf.close()
        print(f"  已添加：{img_path.name}")

    out_doc.save(output_file)
    out_doc.close()
    print(f"\n✅ 图片转 PDF 完成 → {output_file}（共 {len(images)} 页）")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="PDF 拆分 & 合并工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # merge
    merge_p = sub.add_parser("merge", help="合并多个 PDF")
    merge_p.add_argument("-i", "--input", nargs="+", required=True, metavar="FILE", help="输入 PDF 文件列表")
    merge_p.add_argument("-o", "--output", required=True, metavar="FILE", help="输出 PDF 路径")

    # split
    split_p = sub.add_parser("split", help="拆分 PDF")
    split_p.add_argument("-i", "--input", required=True, metavar="FILE", help="输入 PDF 文件")
    split_p.add_argument("-o", "--output", required=True, metavar="DIR", help="输出目录")
    split_p.add_argument(
        "-r", "--ranges", nargs="*", metavar="RANGE",
        help="页码范围，如 1-3 5 7-9（不指定则每页单独拆分）",
    )

    # images2pdf
    img_p = sub.add_parser("images2pdf", help="将目录下所有图片合并为一个 PDF")
    img_p.add_argument("-i", "--input", required=True, metavar="DIR", help="包含图片的目录")
    img_p.add_argument("-o", "--output", required=True, metavar="FILE", help="输出 PDF 路径")

    args = parser.parse_args()

    if args.cmd == "merge":
        merge_pdfs(args.input, args.output)
    elif args.cmd == "split":
        split_pdf(args.input, args.output, args.ranges or None)
    elif args.cmd == "images2pdf":
        images_to_pdf(args.input, args.output)


if __name__ == "__main__":
    main()
