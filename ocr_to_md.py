import sys
import os
from paddleocr import PaddleOCR


def ocr_to_markdown(image_path):
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        lang='ch'
    )
    result = ocr.ocr(image_path)

    lines = []
    for page in result:
        if isinstance(page, dict) and 'rec_texts' in page:
            for text in page['rec_texts']:
                if text.strip():
                    lines.append(text.strip())

    md_content = '\n\n'.join(lines)

    output_path = os.path.splitext(image_path)[0] + '.md'
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f"已保存：{output_path}")
    return output_path


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法：python ocr_to_md.py <图片路径> [图片路径2 ...]")
        sys.exit(1)

    for image_path in sys.argv[1:]:
        ocr_to_markdown(image_path)
