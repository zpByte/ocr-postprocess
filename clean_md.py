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

from bs4 import BeautifulSoup, Tag


def check_pandoc():
    if not shutil.which("pandoc"):
        print("❌ 未找到 pandoc，请先安装：brew install pandoc", file=sys.stderr)
        sys.exit(1)


def default_output_path(input_path: str) -> str:
    base, ext = os.path.splitext(input_path)
    return f"{base}_clean{ext}"


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
            text = cell.get_text(strip=True)

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


def clean_markdown(input_path: str, output_path: Optional[str] = None) -> str:
    """用 pandoc 将富 Markdown 转换为普通 Markdown，返回输出路径。"""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"文件不存在：{input_path}")

    if output_path is None:
        output_path = default_output_path(input_path)

    with open(input_path, "r", encoding="utf-8") as f:
        raw = f.read()

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

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(result)

    print(f"✅ 已转换：{output_path}")
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
