#!/usr/bin/env python3
"""
convert.py — 单文件（图片 / PDF）→ Markdown

用法：
    source venv/bin/activate
    python convert.py 文件路径 [文件路径2 ...]

支持格式：PNG, JPG, JPEG, BMP, TIFF, PDF
输出：同名 .md 文件（保留排版结构）
"""
import sys
import os

DISABLE_CHECK = "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"
os.environ.setdefault(DISABLE_CHECK, "True")

from paddleocr import PPStructureV3


def convert_to_markdown(input_path: str) -> str:
    """将单个图片或 PDF 文件转为 Markdown，返回输出路径。"""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"文件不存在：{input_path}")

    ext = os.path.splitext(input_path)[1].lower()
    supported = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".pdf"}
    if ext not in supported:
        raise ValueError(f"不支持的格式：{ext}（支持：{', '.join(supported)}）")

    pipeline = PPStructureV3(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
    )

    results = pipeline.predict(input=input_path)

    pages_md = []
    for res in results:
        md = res.get("markdown", "")
        if md:
            pages_md.append(md.strip())

    content = "\n\n---\n\n".join(pages_md) if pages_md else ""

    output_path = os.path.splitext(input_path)[0] + ".md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"✅ 已保存：{output_path}")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python convert.py <文件路径> [文件路径2 ...]")
        sys.exit(1)

    for path in sys.argv[1:]:
        try:
            convert_to_markdown(path)
        except Exception as e:
            print(f"❌ 处理 {path} 时出错：{e}", file=sys.stderr)
