"""Cheatsheet Markdown -> Word .docx (DESIGN.md §16.4).

- With pandoc on PATH: pandoc does it, and formulas become real Word equations. Raw HTML / TeX are off
  and `--sandbox` stops it from reading local files (e.g. an image path in the Markdown).
- Without it: a small built-in writer (standard library only) for what cheatsheets contain: headings,
  paragraphs, bullet / numbered lists, bold / italic / code, tables. Formulas stay as their TeX text.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from io import BytesIO
from xml.sax.saxutils import escape

PANDOC_TIMEOUT = 60


def to_docx(md: str) -> tuple[bytes, bool]:
    """The .docx bytes, and whether formulas are real equations (pandoc was used)."""
    exe = shutil.which("pandoc")
    if exe:
        try:
            r = subprocess.run([exe, "--sandbox", "-f", "markdown-raw_html-raw_tex+tex_math_dollars", "-t", "docx", "-o", "-"],
                               input=md.encode(), capture_output=True, timeout=PANDOC_TIMEOUT, check=True)
            if r.stdout[:2] == b"PK":
                return r.stdout, True
        except (subprocess.SubprocessError, OSError):
            pass  # fall back to the built-in writer
    return builtin_docx(md), False


# --- built-in writer ------------------------------------------------------------------------------

_INLINE = re.compile(r"(\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*|_[^_\s][^_]*_|`[^`]+`|\$\$[^$]+\$\$|\$[^$\n]+\$)")


def _runs(text: str) -> str:
    out = []
    for part in _INLINE.split(text):
        if not part:
            continue
        props = ""
        if part.startswith(("**", "__")) and len(part) > 4:
            part, props = part[2:-2], "<w:b/>"
        elif part.startswith("`"):
            part, props = part[1:-1], '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>'
        elif part.startswith("$"):
            part, props = part.strip("$"), '<w:rFonts w:ascii="Cambria Math" w:hAnsi="Cambria Math"/><w:i/>'
        elif part[0] in "*_" and part[-1] == part[0] and len(part) > 2:
            part, props = part[1:-1], "<w:i/>"
        rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
        out.append(f'<w:r>{rpr}<w:t xml:space="preserve">{escape(part)}</w:t></w:r>')
    return "".join(out)


def _p(text: str, style: str | None = None, indent: int = 0, prefix: str = "") -> str:
    ppr = ""
    if style or indent:
        ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + \
              (f'<w:ind w:left="{indent}" w:hanging="260"/>' if indent else "") + "</w:pPr>"
    lead = f'<w:r><w:t xml:space="preserve">{escape(prefix)}</w:t></w:r>' if prefix else ""
    return f"<w:p>{ppr}{lead}{_runs(text)}</w:p>"


def _table(rows: list[list[str]]) -> str:
    border = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
                     for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
    xml = [f'<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>{border}</w:tblBorders></w:tblPr>']
    for i, row in enumerate(rows):
        cells = "".join(f"<w:tc>{_p(f'**{c}**' if i == 0 and c else c)}</w:tc>" for c in row)
        xml.append(f"<w:tr>{cells}</w:tr>")
    xml.append("</w:tbl><w:p/>")
    return "".join(xml)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _body(md: str) -> str:
    lines = md.replace("\r\n", "\n").split("\n")
    out, i, para = [], 0, []

    def flush() -> None:
        if para:
            out.append(_p(" ".join(para)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            flush()
            i += 1
            continue
        if s.startswith("$$"):  # display formula, possibly over several lines
            flush()
            body = [s[2:]]
            while not body[-1].rstrip().endswith("$$") and i + 1 < len(lines):
                i += 1
                body.append(lines[i].strip())
            tex = " ".join(body).strip().removesuffix("$$").strip()
            out.append(_p(f"${tex}$", style="Formula"))
            i += 1
            continue
        if m := re.match(r"(#{1,6})\s+(.*)", s):
            flush()
            level = len(m.group(1))
            out.append(_p(m.group(2).strip().rstrip("#").strip(), style="Title" if level == 1 else f"Heading{min(level - 1, 3)}"))
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(lines) and re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", lines[i + 1].strip()):
            flush()
            rows = [_cells(s)]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            out.append(_table(rows))
            continue
        if m := re.match(r"(\s*)([-*+]|\d+[.)])\s+(.*)", line):
            flush()
            depth = len(m.group(1).expandtabs(4)) // 2
            bullet = "• " if m.group(2) in "-*+" else m.group(2) + " "
            out.append(_p(m.group(3), indent=360 + 360 * depth, prefix=bullet))
            i += 1
            continue
        if s.startswith(">"):
            flush()
            out.append(_p(s.lstrip("> ").strip(), indent=360))
            i += 1
            continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s):
            flush()
            i += 1
            continue
        para.append(s)
        i += 1
    flush()
    return "".join(out)


_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="PingFang SC"/><w:sz w:val="20"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="40" w:line="260" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="120"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="160" w:after="40"/><w:pBdr><w:bottom w:val="single" w:sz="4" w:space="1" w:color="AAAAAA"/></w:pBdr></w:pPr><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="100" w:after="20"/></w:pPr><w:rPr><w:b/><w:sz w:val="21"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/></w:pPr><w:rPr><w:b/><w:i/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Formula"><w:name w:val="Formula"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/></w:pPr></w:style>
</w:styles>"""

_DOC = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="567" w:right="567" w:bottom="567" w:left="567" '
        'w:header="0" w:footer="0" w:gutter="0"/><w:cols w:num="2" w:space="340"/></w:sectPr></w:body></w:document>')

_TYPES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
          '</Types>')
_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
         '</Relationships>')
_DOC_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
             '</Relationships>')


def builtin_docx(md: str) -> bytes:
    """A two-column A4 .docx in the print layout's spirit (narrow margins, 10pt)."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("word/document.xml", _DOC.format(body=_body(md)))
    return buf.getvalue()
