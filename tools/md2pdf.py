#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
md2pdf · 把 Markdown 报告转 PDF(Chrome headless print-to-pdf)
==============================================================
配方(sim-report-pdf-recipe):md --markdown库--> html --Chrome headless--> PDF(Skia)。
仓库无 PDF 工具链,故自带:python-markdown(tables/fenced_code)渲染 + 系统 Chrome 打印。
CJK 用 PingFang SC;表格/代码块/热力网格照排。

用法:
  python3 tools/md2pdf.py <in.md> [out.pdf]          # 单个;省略 out 则同名 .pdf 落 reports/match-sims/pdf/
  python3 tools/md2pdf.py <in.md> --out-dir <dir>    # 指定输出目录

依赖:pip 的 markdown;/Applications/Google Chrome.app。纯本地 macOS。
"""
import sys
import os
import argparse
import subprocess
import tempfile
import html as _html

import markdown

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
@page { size: A4; margin: 14mm 12mm; }
* { box-sizing: border-box; }
body {
  font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", -apple-system, sans-serif;
  font-size: 12px; line-height: 1.5; color: #1a1a1a; margin: 0;
}
h1 { font-size: 20px; border-bottom: 2px solid #333; padding-bottom: 6px; margin: 0 0 10px; }
h2 { font-size: 15px; margin: 16px 0 8px; padding-left: 6px; border-left: 4px solid #2c7; }
h3 { font-size: 13px; margin: 12px 0 6px; }
blockquote {
  margin: 8px 0; padding: 6px 12px; background: #f6f8fa;
  border-left: 3px solid #bbb; color: #333; font-size: 11px;
}
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 11px; }
th, td { border: 1px solid #d0d0d0; padding: 4px 7px; text-align: left; }
th { background: #f0f3f6; font-weight: 600; }
tr:nth-child(even) td { background: #fafbfc; }
code { font-family: "SF Mono", Menlo, monospace; font-size: 10.5px;
       background: #f0f0f0; padding: 1px 4px; border-radius: 3px; }
pre { background: #f6f8fa; padding: 10px; border-radius: 5px; overflow-x: auto;
      line-height: 1.35; border: 1px solid #e0e0e0; }
pre code { background: none; padding: 0; font-size: 10px; }
hr { border: none; border-top: 1px solid #ddd; margin: 12px 0; }
strong { color: #000; }
ul, ol { margin: 6px 0; padding-left: 22px; }
li { margin: 2px 0; }
"""


def md_to_pdf(md_path, out_path):
    with open(md_path, encoding="utf-8") as f:
        text = f.read()
    body = markdown.markdown(
        text, extensions=["tables", "fenced_code", "sane_lists", "nl2br"]
    )
    doc = f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tf:
        tf.write(doc)
        html_path = tf.name
    try:
        subprocess.run(
            [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
             f"--print-to-pdf={out_path}", f"file://{html_path}"],
            check=True, capture_output=True, timeout=90,
        )
    finally:
        os.unlink(html_path)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Markdown → PDF (Chrome headless)")
    ap.add_argument("md", help="输入 .md")
    ap.add_argument("out", nargs="?", help="输出 .pdf(省略则同名落 --out-dir)")
    ap.add_argument("--out-dir", default="reports/match-sims/pdf",
                    help="省略 out 时的输出目录(默认 reports/match-sims/pdf)")
    a = ap.parse_args()
    if a.out:
        out = a.out
    else:
        base = os.path.splitext(os.path.basename(a.md))[0] + ".pdf"
        os.makedirs(a.out_dir, exist_ok=True)
        out = os.path.join(a.out_dir, base)
    md_to_pdf(a.md, out)
    sz = os.path.getsize(out)
    print(f"✓ {out}  ({sz//1024} KB)")


if __name__ == "__main__":
    main()
