#!/usr/bin/env python3
"""
clean_md.py — 把 PaddleOCR 输出的"富 Markdown"（含 HTML table / inline style）
              转为普通 Markdown，方便上传到云文档。

处理流程：
  1. 用 bs4 预处理 HTML table：
     - 给缺失 <thead> 的表格补上（否则 pandoc 不识别表头）
     - 把 colspan/rowspan 展开为普通单元格（pandoc 不支持合并单元格 → pipe table）
  2. markdown+raw_html → html（pandoc 完整解析 HTML table）
  3. html → gfm（输出 pipe table，inline style 全剥离）

用法：
    source venv/bin/activate
    python clean_md.py 输入.md            # 另存为「输入_clean.md」
    python clean_md.py 输入.md 输出.md    # 另存为指定路径
    python clean_md.py *.md              # 批量处理（各自生成 _clean.md）

依赖：pandoc（brew install pandoc）、beautifulsoup4（pip install beautifulsoup4）
"""
import sys
import os
import re
import subprocess
import shutil
from typing import Optional
from pathlib import Path

from bs4 import BeautifulSoup, Tag

# 导入 download_images 的 process_md_file 函数
try:
    from download_images import process_md_file as download_images_process
except ImportError:
    download_images_process = None


def check_pandoc():
    if not shutil.which("pandoc"):
        print("❌ 未找到 pandoc，请先安装：brew install pandoc", file=sys.stderr)
        sys.exit(1)


def extract_title(content: str) -> Optional[str]:
    """提取 Markdown 文档中的第一个标题（# 开头的行）。"""
    for line in content.split('\n'):
        line = line.strip()
        if line.startswith('#'):
            # 移除 # 符号和前后的空格
            title = line.lstrip('#').strip()
            if title:
                return title
    return None


def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符，保留中文、英文、数字、空格、下划线和连字符。"""
    # 替换文件名中不允许的字符为下划线
    # macOS/Windows/Linux 都不允许: / \ : * ? " < > |
    # 我们还移除一些控制字符和其他特殊字符
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    # 移除前后空格和点（避免隐藏文件问题）
    name = name.strip(' .')
    # 如果清理后为空，返回默认名称
    return name if name else '未命名'


def default_output_path(input_path: str, title: Optional[str] = None) -> str:
    """生成输出文件路径。
    
    如果提供了 title，使用标题作为文件名；否则使用原文件名加 _clean 后缀。
    """
    input_dir = os.path.dirname(input_path)
    
    if title:
        # 使用标题作为文件名
        clean_title = sanitize_filename(title)
        filename = f"{clean_title}.md"
    else:
        # 使用原文件名加 _clean 后缀
        base, _ = os.path.splitext(input_path)
        filename = f"{os.path.basename(base)}_clean.md"
    
    if input_dir:
        return os.path.join(input_dir, filename)
    else:
        return filename


def _cell_text(cell: Tag) -> str:
    """提取单元格文本。若含有序列表（<ol>），为每个 <li> 补上序号。"""
    ol = cell.find("ol")
    if ol:
        parts = []
        for i, li in enumerate(ol.find_all("li", recursive=False), start=1):
            li_text = li.get_text(strip=True)
            # li_text 内可能已包含 "2. xxx 3. xxx"（OCR 把多条塞进一个 li）
            # 只在开头加上序号（如果还没有）
            if not li_text.startswith(f"{i}."):
                li_text = f"{i}. {li_text}"
            parts.append(li_text)
        return " ".join(parts)
    return cell.get_text(strip=True)


def _expand_table(table: Tag) -> None:
    """
    原地修改一个 <table> Tag：
    1. 展开 colspan / rowspan（用空 <td> 填充）
    2. 若无 <thead>，把第一行包进 <thead>
    """
    # ---------- 收集所有行 ----------
    rows = table.find_all("tr")
    if not rows:
        return

    # 把行组织成二维 grid（考虑 rowspan）
    grid: list[list[str]] = []
    # rowspan 占位：{(row_idx, col_idx): 剩余行数}
    pending_rowspan: dict[tuple[int, int], int] = {}

    for r_idx, row in enumerate(rows):
        cells = [td for td in row.children if isinstance(td, Tag) and td.name in ("td", "th")]
        new_row: list[str] = []
        c_idx = 0

        for cell in cells:
            # 跳过被 rowspan 占用的列
            while (r_idx, c_idx) in pending_rowspan:
                new_row.append("")
                # 减少剩余行数
                remaining = pending_rowspan[(r_idx, c_idx)] - 1
                if remaining > 0:
                    pending_rowspan[(r_idx + 1, c_idx)] = remaining
                del pending_rowspan[(r_idx, c_idx)]
                c_idx += 1

            colspan = int(cell.get("colspan", 1))
            rowspan = int(cell.get("rowspan", 1))
            text = _cell_text(cell)

            # 填充 colspan
            for ci in range(colspan):
                new_row.append(text if ci == 0 else "")
                if rowspan > 1:
                    for ri in range(1, rowspan):
                        pending_rowspan[(r_idx + ri, c_idx + ci)] = rowspan - ri

            c_idx += colspan

        # 补上剩余 rowspan 占位（行尾）
        while (r_idx, c_idx) in pending_rowspan:
            new_row.append("")
            remaining = pending_rowspan[(r_idx, c_idx)] - 1
            if remaining > 0:
                pending_rowspan[(r_idx + 1, c_idx)] = remaining
            del pending_rowspan[(r_idx, c_idx)]
            c_idx += 1

        grid.append(new_row)

    if not grid:
        return

    # ---------- 重建 table HTML ----------
    max_cols = max(len(r) for r in grid)

    # pad 短行
    for r in grid:
        while len(r) < max_cols:
            r.append("")

    # 确定表头行数（有 <thead> 就用，没有就取第一行）
    thead = table.find("thead")
    if thead:
        thead_row_count = len(thead.find_all("tr"))
    else:
        thead_row_count = 1

    def make_row(cells: list[str], is_header: bool) -> str:
        tag = "th" if is_header else "td"
        inner = "".join(f"<{tag}>{c}</{tag}>" for c in cells)
        return f"<tr>{inner}</tr>"

    header_rows = grid[:thead_row_count]
    body_rows = grid[thead_row_count:]

    thead_html = "<thead>" + "".join(make_row(r, True) for r in header_rows) + "</thead>"
    tbody_html = "<tbody>" + "".join(make_row(r, False) for r in body_rows) + "</tbody>"

    new_table = BeautifulSoup(f"<table>{thead_html}{tbody_html}</table>", "html.parser")
    table.replace_with(new_table.find("table"))


def _is_empty_pipe_row(line: str) -> bool:
    """判断一个 pipe table 行是否全部单元格为空。"""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return bool(cells) and all(c == "" for c in cells)


def _is_separator_row(line: str) -> bool:
    """判断一行是否是 pipe table 分隔符行（|:---:|）。"""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    return bool(cells) and all(re.match(r"^:?-+:?$", c) for c in cells if c)


def fix_empty_pipe_table_headers(text: str) -> str:
    """
    PaddleOCR 输出的 pipe table 有时表头行是全空的：
        |   |   |   |        ← 空表头（删除）
        |:-:|:-:|:-:|        ← 分隔符（删除，后面重新插入）
        | 实际表头 | ... |    ← 提升为表头
        | 数据行 | ... |
    修复：删除空表头行和紧跟的分隔符，让第一个数据行成为真正的表头，
          并在其后插入标准分隔符行。
    """
    lines = text.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("|") and _is_empty_pipe_row(line):
            # 下一行是分隔符行
            if i + 1 < len(lines) and _is_separator_row(lines[i + 1]):
                # 跳过空表头行和分隔符行
                i += 2
                # 把后面第一个数据行作为新表头，并在其后插入分隔符
                if i < len(lines) and lines[i].startswith("|"):
                    header_line = lines[i]
                    # 生成对应列数的标准分隔符行
                    col_count = len(header_line.strip().strip("|").split("|"))
                    sep = "|" + "|".join(["---|"] * col_count) if col_count else "|----|"
                    sep = "|" + "|".join(["---"] * col_count) + "|"
                    result.append(header_line)
                    result.append(sep)
                    i += 1
                continue
        result.append(line)
        i += 1
    return "\n".join(result)


def preprocess_html_tables(text: str) -> str:
    """找到 markdown 中所有 raw HTML table，用 bs4 展开 colspan/rowspan 并注入 thead。"""
    def fix_block(m: re.Match) -> str:
        soup = BeautifulSoup(m.group(0), "html.parser")
        table = soup.find("table")
        if table:
            _expand_table(table)
        return str(soup)

    return re.sub(r"<table[\s\S]*?</table>", fix_block, text, flags=re.IGNORECASE)


def strip_div_tags(text: str) -> str:
    r"""
    移除 Markdown 中所有 <div ...>...</div> 标签，但保留内部内容。

    PaddleOCR 有时会输出带样式属性的 div 标签，如：
        <div style="text-align: center;"><div style="text-align: center;">表1-2：Q1月度消耗走势</div> </div>

    这些标签在标准 Markdown 中没有意义，直接剥离即可。
    注意：不用 bs4 解析整个文档，避免 & 被转义为 &amp;。
    """
    # 用正则移除 <div ...> 和 </div> 标签，保留内容
    text = re.sub(r'<div[^>]*>', '', text)
    text = re.sub(r'</div>', '', text)
    return text


def fix_latex_markup(text: str) -> str:
    r"""
    清洗 PaddleOCR / pandoc 输出中残留的 LaTeX 样式标注，转换为普通 Markdown：

    - $ \underline{\text{XXX}} $  →  **XXX**   （下划线强调 → 加粗）
    - $ \uwave{\text{XXX}} $      →  **XXX**   （波浪线强调 → 加粗）
    - $ \dashuline{XXX} $         →  **XXX**   （虚线下划线 → 加粗，OCR 可能漏掉 \）
    - $ dashuline{XXX} $          →  **XXX**   （同上，OCR 漏识别反斜杠）
    - $ \underset{.}{X} $         →  X         （下点标注 → 纯文字）
    - \$ ... \$（pandoc 转义版本，同上处理）

    行首独立 LaTeX 标题（如 "##### $ \underline{\text{XXX}} $"）会在标题符号保留
    后，把 LaTeX 部分替换为加粗文字，整体结构不变。
    """
    # 统一处理：先把 \$ 反转义为 $ 以便后续统一匹配
    # pandoc 在 pipe table 单元格内会把 $ 转义为 \$
    text = re.sub(r'\\\$', '$', text)

    # 1. $ \underline{\text{内容}} $  →  **内容**
    text = re.sub(
        r'\$\s*\\underline\{\\text\{([^}]*)\}\}\s*\$',
        lambda m: f'**{m.group(1).strip()}**',
        text,
    )

    # 2. $ \uwave{\text{内容}} $  →  **内容**
    text = re.sub(
        r'\$\s*\\uwave\{\\text\{([^}]*)\}\}\s*\$',
        lambda m: f'**{m.group(1).strip()}**',
        text,
    )

    # 2b. $ \dashuline{内容} $ 或 $ dashuline{内容} $  →  **内容**
    #     OCR 有时把 \dashuline 的反斜杠漏识别，需同时覆盖两种形态
    #     内容可能含 \text{...} 或直接是文字
    text = re.sub(
        r'\$\s*\\?dashuline\{(?:\\text\{)?([^}]*)(?:\})?\}\s*\$',
        lambda m: f'**{m.group(1).strip()}**',
        text,
    )

    # 3. $ \underset{任意}{内容} $  →  内容
    #    如 $ \underset{\cdot}{配} $  →  配
    text = re.sub(
        r'\$\s*\\underset\{[^}]*\}\{([^}]*)\}\s*\$',
        lambda m: m.group(1).strip(),
        text,
    )

    # 4. 兜底：清理剩余孤立的空 $ $ 或 $  $（OCR 有时产生空数学块）
    text = re.sub(r'\$\s*\$', '', text)

    return text


def format_numbers_with_commas(text: str) -> str:
    r"""
    为 ≥ 10000 的数字添加千位逗号分隔符。

    跳过以下情况：
    - 4 位数年份（1900–2099）
    - 已含逗号的数字
    - 前后紧跟字母 / 下划线的编号（如 v1000、id_10000）
    - Markdown 链接 / 图片中的数字（URL 内）
    - 代码块内的数字

    示例：
        12345   → 12,345
        1234567 → 1,234,567
        2024    → 2024（年份，跳过）
        12345.6 → 12,345.6
    """
    # 先把代码块保护起来，避免格式化代码中的数字
    code_blocks: list[str] = []
    CODE_PH = "\x00CODE_BLOCK_{idx}\x00"

    def save_code(m: re.Match) -> str:
        code_blocks.append(m.group(0))
        return CODE_PH.format(idx=len(code_blocks) - 1)

    # 匹配围栏式代码块（```...```）和行内代码（`...`）
    protected = re.sub(r'```[\s\S]*?```|`[^`\n]+`', save_code, text)

    def add_commas(m: re.Match) -> str:
        num_str = m.group(0)
        # 跳过 4 位年份（1900–2099）
        if re.fullmatch(r'(19|20)\d{2}', num_str):
            return num_str
        # 分离小数部分
        if '.' in num_str:
            int_part, dec_part = num_str.split('.', 1)
        else:
            int_part, dec_part = num_str, None

        val = int(int_part)
        if val < 10000:
            return num_str  # 不足万位，跳过

        formatted_int = f"{val:,}"
        return f"{formatted_int}.{dec_part}" if dec_part is not None else formatted_int

    # 匹配独立数字（前后不能是字母、下划线、句点、斜线、逗号）
    # 确保不在 URL 内（简单地排除前面是 / 或 = 的情况）
    protected = re.sub(
        r'(?<![a-zA-Z_./=\-#,\d])(\d{4,}(?:\.\d+)?)(?![a-zA-Z_,\d])',
        lambda m: add_commas(m),
        protected,
    )

    # 还原代码块
    def restore_code(m: re.Match) -> str:
        return code_blocks[int(m.group(1))]

    return re.sub(r'\x00CODE_BLOCK_(\d+)\x00', restore_code, protected)


def fix_leading_period(text: str) -> str:
    r"""
    将行首的无序列表误识别符号转换为 Markdown 无序列表符号（-）。

    PaddleOCR 有时把无序列表的项目符号识别为：
    - 中文句号 "。"
    - 实心圆点 "•"（U+2022 BULLET）

    规则：
    - 仅处理行首的上述符号（含行首有空格后跟符号的情况）
    - 保留符号后原有内容，前置改为"- "
    - 若符号后紧跟空格，也一并去除多余空格
    - 行中间出现的句号 / 圆点不受影响
    """
    lines = text.split('\n')
    result = []
    for line in lines:
        # 匹配行首（含前缀空格）+ 中文句号 或 实心圆点 •
        m = re.match(r'^(\s*)[。•]\s*(.*)', line)
        if m:
            indent = m.group(1)
            content = m.group(2)
            result.append(f'{indent}- {content}')
        else:
            result.append(line)
    return '\n'.join(result)


def clean_markdown(input_path: str, output_path: Optional[str] = None) -> str:
    """用 pandoc 将富 Markdown 转换为普通 Markdown，返回输出路径。"""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"文件不存在：{input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        raw = f.read()

    # 提取标题用于文件命名
    title = extract_title(raw)
    
    if output_path is None:
        output_path = default_output_path(input_path, title)

    # 1. 修复纯 pipe table 的空表头行（PaddleOCR bug）
    text = fix_empty_pipe_table_headers(raw)

    # 2. 分段处理：把 HTML table 块单独抽出，用 pandoc 转换；其余部分直接保留
    # 用占位符替换每个 HTML table，pandoc 转换后再还原
    html_tables: list[str] = []
    PLACEHOLDER = "\n\nHTMLTABLE_PLACEHOLDER_{idx}\n\n"

    def extract_table(m: re.Match) -> str:
        # bs4 展开 colspan/rowspan
        soup = BeautifulSoup(m.group(0), "html.parser")
        table = soup.find("table")
        if table:
            _expand_table(table)
        html_tables.append(str(soup))
        return PLACEHOLDER.format(idx=len(html_tables) - 1)

    text_with_placeholders = re.sub(
        r"<table[\s\S]*?</table>", extract_table, text, flags=re.IGNORECASE
    )

    # 3. 逐个将 HTML table 转为 pipe table（两步 pandoc：html_table → html → gfm）
    converted_tables: list[str] = []
    for html_table in html_tables:
        step1 = subprocess.run(
            ["pandoc", "--from", "html", "--to", "gfm", "--wrap", "none"],
            input=html_table,
            capture_output=True,
            text=True,
        )
        if step1.returncode != 0:
            # 转换失败就保留原始 HTML
            converted_tables.append(html_table)
        else:
            converted_tables.append(step1.stdout.strip())

    # 4. 还原占位符
    def restore(m: re.Match) -> str:
        idx = int(m.group(1))
        return "\n\n" + converted_tables[idx] + "\n\n"

    result = re.sub(r"HTMLTABLE_PLACEHOLDER_(\d+)", restore, text_with_placeholders)

    # pandoc 会把 pipe table 单元格内行首的 "1." 转义为 "1\."
    # 但单元格内没有行首语义，直接还原
    result = re.sub(r'(\|\s*)(\d+)\\\.', r'\1\2.', result)

    # pandoc 会把 pipe table 单元格内独立的 "-" 转义为 "\-"，直接全局还原
    result = result.replace(r'\-', '-')

    # 5. 移除 <div> 标签（保留内容）
    result = strip_div_tags(result)

    # 6. 清洗 LaTeX 样式标注
    result = fix_latex_markup(result)

    # 7. 修复行首中文句号 → Markdown 无序列表符号
    result = fix_leading_period(result)

    # 8. 为 ≥ 10000 的数字添加千位逗号分隔符
    result = format_numbers_with_commas(result)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(result)

    print(f"✅ 已转换：{output_path}")

    # 自动调用 download_images 下载图片（可通过环境变量 SKIP_DOWNLOAD_IMAGES=1 禁用）
    if os.environ.get("SKIP_DOWNLOAD_IMAGES") == "1":
        print("ℹ️  已设置 SKIP_DOWNLOAD_IMAGES=1，跳过图片下载")
    elif download_images_process is not None:
        print("📥 正在自动下载图片...")
        try:
            download_images_process(output_path)
        except Exception as e:
            print(f"⚠️  图片下载过程中出错：{e}", file=sys.stderr)
    else:
        print("ℹ️  未找到 download_images.py，跳过图片下载")

    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python clean_md.py <输入.md> [输出.md]")
        print("      python clean_md.py *.md          # 批量，各自生成 _clean.md")
        sys.exit(1)

    check_pandoc()

    if len(sys.argv) == 2:
        try:
            clean_markdown(sys.argv[1])
        except Exception as e:
            print(f"❌ {e}", file=sys.stderr)
            sys.exit(1)
    elif len(sys.argv) == 3 and sys.argv[2].endswith(".md"):
        try:
            clean_markdown(sys.argv[1], sys.argv[2])
        except Exception as e:
            print(f"❌ {e}", file=sys.stderr)
            sys.exit(1)
    else:
        errors = 0
        for path in sys.argv[1:]:
            try:
                clean_markdown(path)
            except Exception as e:
                print(f"❌ 处理 {path} 时出错：{e}", file=sys.stderr)
                errors += 1
        if errors:
            sys.exit(1)
