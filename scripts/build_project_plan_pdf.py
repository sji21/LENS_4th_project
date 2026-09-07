"""Build the shareable LENS project-plan PDF from Markdown."""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "docs" / "planning" / "project-plan.md"
OUTPUT = PROJECT_ROOT / "docs" / "planning" / "jeonseon-project-plan.pdf"
load_dotenv(PROJECT_ROOT / ".env", override=False)
FONT_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf"),
)

NAVY = colors.HexColor("#17365D")
BLUE = colors.HexColor("#1E6AA8")
SKY = colors.HexColor("#EAF3F9")
PALE = colors.HexColor("#F5F8FB")
INK = colors.HexColor("#17212B")
MUTED = colors.HexColor("#5D6B78")
LINE = colors.HexColor("#CBD8E3")
WHITE = colors.white


def find_korean_font() -> Path:
    """환경 변수와 운영체제별 기본 경로에서 PDF용 한국어 글꼴을 찾는다."""

    configured = os.getenv("KOREAN_FONT_PATH", "").strip()
    candidates = (
        (Path(configured).expanduser(), *FONT_CANDIDATES)
        if configured
        else FONT_CANDIDATES
    )
    font_path = next((path for path in candidates if path.is_file()), None)
    if font_path is None:
        raise RuntimeError(
            "한국어 PDF 글꼴을 찾지 못했습니다. KOREAN_FONT_PATH에 TTF 글꼴 경로를 설정하세요."
        )
    return font_path


def register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("Korean", str(find_korean_font())))


def normalize(value: str) -> str:
    return (
        value.replace("\u00a0", " ")
        .replace("\u2011", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )


def inline_markup(value: str) -> str:
    value = normalize(value.strip())
    tokens: dict[str, str] = {}

    def hold_code(match: re.Match[str]) -> str:
        key = f"@@CODE{len(tokens)}@@"
        tokens[key] = f'<font name="Korean" color="#0F5D8C">{html.escape(match.group(1))}</font>'
        return key

    value = re.sub(r"`([^`]+)`", hold_code, value)
    value = html.escape(value)
    value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value)
    for key, replacement in tokens.items():
        value = value.replace(key, replacement)
    return value.replace("  ", " ")


def make_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "body": ParagraphStyle(
            "BodyK",
            parent=base["BodyText"],
            fontName="Korean",
            fontSize=9.2,
            leading=15,
            textColor=INK,
            spaceAfter=5,
            wordWrap="CJK",
        ),
        "h1": ParagraphStyle(
            "H1K",
            parent=base["Heading1"],
            fontName="Korean",
            fontSize=21,
            leading=29,
            textColor=NAVY,
            spaceBefore=3,
            spaceAfter=12,
            wordWrap="CJK",
        ),
        "h2": ParagraphStyle(
            "H2K",
            parent=base["Heading2"],
            fontName="Korean",
            fontSize=15,
            leading=21,
            textColor=NAVY,
            spaceBefore=12,
            spaceAfter=7,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "h3": ParagraphStyle(
            "H3K",
            parent=base["Heading3"],
            fontName="Korean",
            fontSize=11.5,
            leading=17,
            textColor=BLUE,
            spaceBefore=9,
            spaceAfter=5,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "quote": ParagraphStyle(
            "QuoteK",
            parent=base["BodyText"],
            fontName="Korean",
            fontSize=9,
            leading=14,
            leftIndent=11,
            rightIndent=7,
            borderColor=BLUE,
            borderWidth=0,
            borderPadding=(7, 9, 7, 10),
            backColor=SKY,
            textColor=NAVY,
            spaceAfter=8,
            wordWrap="CJK",
        ),
        "list": ParagraphStyle(
            "ListK",
            parent=base["BodyText"],
            fontName="Korean",
            fontSize=8.9,
            leading=14,
            textColor=INK,
            wordWrap="CJK",
        ),
        "code": ParagraphStyle(
            "CodeK",
            parent=base["Code"],
            fontName="Korean",
            fontSize=7.5,
            leading=11,
            leftIndent=7,
            rightIndent=7,
            borderColor=LINE,
            borderWidth=0.5,
            borderPadding=7,
            backColor=PALE,
            textColor=INK,
            spaceBefore=2,
            spaceAfter=8,
        ),
        "table": ParagraphStyle(
            "TableK",
            parent=base["BodyText"],
            fontName="Korean",
            fontSize=7.1,
            leading=10.5,
            textColor=INK,
            wordWrap="CJK",
        ),
        "table_small": ParagraphStyle(
            "TableSmallK",
            parent=base["BodyText"],
            fontName="Korean",
            fontSize=5.7,
            leading=8.2,
            textColor=INK,
            wordWrap="CJK",
        ),
        "table_head": ParagraphStyle(
            "TableHeadK",
            parent=base["BodyText"],
            fontName="Korean",
            fontSize=7.1,
            leading=10,
            textColor=WHITE,
            alignment=TA_CENTER,
            wordWrap="CJK",
        ),
        "table_head_small": ParagraphStyle(
            "TableHeadSmallK",
            parent=base["BodyText"],
            fontName="Korean",
            fontSize=5.6,
            leading=7.7,
            textColor=WHITE,
            alignment=TA_CENTER,
            wordWrap="CJK",
        ),
        "cover_title": ParagraphStyle(
            "CoverTitleK",
            parent=base["Title"],
            fontName="Korean",
            fontSize=30,
            leading=40,
            textColor=NAVY,
            alignment=TA_LEFT,
            wordWrap="CJK",
        ),
        "cover_sub": ParagraphStyle(
            "CoverSubK",
            parent=base["BodyText"],
            fontName="Korean",
            fontSize=13,
            leading=21,
            textColor=BLUE,
            wordWrap="CJK",
        ),
        "cover_meta": ParagraphStyle(
            "CoverMetaK",
            parent=base["BodyText"],
            fontName="Korean",
            fontSize=9.2,
            leading=16,
            textColor=MUTED,
            wordWrap="CJK",
        ),
        "toc": ParagraphStyle(
            "TocK",
            parent=base["BodyText"],
            fontName="Korean",
            fontSize=10,
            leading=17,
            textColor=INK,
            leftIndent=5,
            wordWrap="CJK",
        ),
    }


def page_decor(canvas, doc) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(18 * mm, height - 15 * mm, width - 18 * mm, height - 15 * mm)
    canvas.setFont("Korean", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, height - 11.5 * mm, "LENS 프로젝트 기획서")
    canvas.drawRightString(width - 18 * mm, 11 * mm, f"{doc.page}")
    canvas.setFillColor(BLUE)
    canvas.rect(18 * mm, 10.5 * mm, 10 * mm, 0.7 * mm, fill=1, stroke=0)
    canvas.restoreState()


def cover_decor(canvas, doc) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 16 * mm, width, 16 * mm, fill=1, stroke=0)
    canvas.setFillColor(BLUE)
    canvas.rect(0, 0, 9 * mm, height, fill=1, stroke=0)
    canvas.setFillColor(SKY)
    canvas.circle(width - 22 * mm, 27 * mm, 26 * mm, fill=1, stroke=0)
    canvas.restoreState()


def column_widths(headers: list[str], usable: float) -> list[float]:
    count = len(headers)
    if count == 2:
        return [usable * 0.28, usable * 0.72]
    if count == 3:
        return [usable * 0.22, usable * 0.35, usable * 0.43]
    if count == 4:
        return [usable * 0.17, usable * 0.20, usable * 0.38, usable * 0.25]
    if count == 5:
        return [usable * 0.10, usable * 0.11, usable * 0.31, usable * 0.19, usable * 0.29]
    weights = [max(6, min(24, len(normalize(h)) + 4)) for h in headers]
    total = sum(weights)
    return [usable * weight / total for weight in weights]


def build_table(rows: list[list[str]], styles: dict[str, ParagraphStyle], usable: float) -> Table:
    headers = rows[0]
    small = len(headers) >= 6
    body_style = styles["table_small" if small else "table"]
    head_style = styles["table_head_small" if small else "table_head"]
    data = []
    for ridx, row in enumerate(rows):
        style = head_style if ridx == 0 else body_style
        padded = row + [""] * (len(headers) - len(row))
        data.append([Paragraph(inline_markup(cell), style) for cell in padded[: len(headers)]])
    table = Table(data, colWidths=column_widths(headers, usable), repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.35, LINE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, PALE]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4 if not small else 2.4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4 if not small else 2.4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def is_separator_row(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def parse_markdown(lines: list[str], styles: dict[str, ParagraphStyle], usable: float):
    story = []
    index = 0
    first_h1 = True
    while index < len(lines):
        raw = lines[index].rstrip()
        stripped = raw.strip()
        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            code_lines = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(normalize(lines[index].rstrip()))
                index += 1
            story.append(Preformatted("\n".join(code_lines), styles["code"]))
            index += 1
            continue

        if stripped.startswith("|") and index + 1 < len(lines) and is_separator_row(lines[index + 1]):
            rows = [[cell.strip() for cell in stripped.strip("|").split("|")]]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append([cell.strip() for cell in lines[index].strip().strip("|").split("|")])
                index += 1
            table_group = [build_table(rows, styles, usable), Spacer(1, 7)]
            story.append(KeepTogether(table_group) if len(rows) <= 8 else table_group[0])
            if len(rows) > 8:
                story.append(table_group[1])
            continue

        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            level = len(heading.group(1))
            if level == 1 and first_h1:
                first_h1 = False
                index += 1
                continue
            story.append(Paragraph(inline_markup(heading.group(2)), styles[f"h{level}"]))
            if level == 2:
                story.append(HRFlowable(width="100%", thickness=0.7, color=LINE, spaceAfter=5))
            index += 1
            continue

        if stripped.startswith(">"):
            quote_lines = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip()[1:].strip())
                index += 1
            story.append(Paragraph(inline_markup(" ".join(quote_lines)), styles["quote"]))
            continue

        bullet_match = re.match(r"^-\s+(?:\[([ xX])\]\s+)?(.+)$", stripped)
        number_match = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if bullet_match or number_match:
            ordered = bool(number_match)
            items = []
            item_number = 1
            while index < len(lines):
                candidate = lines[index].strip()
                match = re.match(r"^(\d+)\.\s+(.+)$", candidate) if ordered else re.match(
                    r"^-\s+(?:\[([ xX])\]\s+)?(.+)$", candidate
                )
                if not match:
                    break
                if ordered:
                    value = match.group(2)
                    prefix = f"{item_number}."
                    item_number += 1
                else:
                    check = match.group(1)
                    value = match.group(2)
                    if check is not None:
                        prefix = "■" if check.lower() == "x" else "□"
                    else:
                        prefix = "•"
                items.append(
                    Paragraph(
                        f'<font color="#1E6AA8">{prefix}</font>&nbsp;&nbsp;{inline_markup(value)}',
                        styles["list"],
                    )
                )
                index += 1
            story.extend(items)
            story.append(Spacer(1, 5))
            continue

        paragraph_lines = [stripped]
        index += 1
        while index < len(lines):
            candidate = lines[index].strip()
            if not candidate:
                break
            if (
                candidate.startswith(("#", ">", "```", "|", "- "))
                or re.match(r"^\d+\.\s+", candidate)
            ):
                break
            paragraph_lines.append(candidate)
            index += 1
        story.append(Paragraph(inline_markup(" ".join(paragraph_lines)), styles["body"]))
    return story


def build_pdf() -> Path:
    register_fonts()
    styles = make_styles()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    page_width, page_height = A4
    margin_x = 18 * mm
    content_width = page_width - 2 * margin_x
    frame = Frame(margin_x, 17 * mm, content_width, page_height - 35 * mm, id="body")
    cover_frame = Frame(24 * mm, 22 * mm, page_width - 46 * mm, page_height - 43 * mm, id="cover")
    doc = BaseDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=margin_x,
        rightMargin=margin_x,
        topMargin=18 * mm,
        bottomMargin=17 * mm,
        title="LENS 프로젝트 기획서",
        author="LENS 프로젝트 팀",
        subject="주택임대차 법령 근거 기반 RAG 프로젝트 실행 기획서",
    )
    doc.addPageTemplates(
        [
            PageTemplate(id="Cover", frames=[cover_frame], onPage=cover_decor, autoNextPageTemplate="Body"),
            PageTemplate(id="Body", frames=[frame], onPage=page_decor),
        ]
    )

    story = [
        Spacer(1, 42 * mm),
        Paragraph("LENS", styles["cover_title"]),
        Paragraph("프로젝트 실행 기획서", styles["cover_title"]),
        Spacer(1, 9 * mm),
        HRFlowable(width="34%", thickness=3, color=BLUE, hAlign="LEFT"),
        Spacer(1, 8 * mm),
        Paragraph("주택임대차 법령 근거 기반<br/>전세계약 점검 RAG 질의응답 시스템", styles["cover_sub"]),
        Spacer(1, 22 * mm),
        Paragraph(
            "문서 근거로 확인되는 내용만 답하고,<br/>근거가 부족하면 보류하며,<br/>개별 법률 판단은 명확히 거절합니다.",
            styles["quote"],
        ),
        Spacer(1, 18 * mm),
        Paragraph("Version 1.0<br/>작성 기준일 2026-08-26<br/>팀 공유용", styles["cover_meta"]),
        PageBreak(),
        Paragraph("목차", styles["h1"]),
    ]

    toc_items = [
        "1. 프로젝트 정의와 문제",
        "2. 목표, 비목표와 답변 정책",
        "3. 데이터 범위와 최소 구현",
        "4. 시스템 아키텍처와 기술 선택",
        "5. 검색·생성·안전 정책",
        "6. 평가 계획과 Gate",
        "7. 10일 WBS와 저장소 적용",
        "8. 리스크, 제출물, 발표와 팀 합의",
    ]
    story.append(
        Table(
            [[Paragraph(item, styles["toc"])] for item in toc_items],
            colWidths=[content_width],
            style=TableStyle(
                [
                    ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ]
            ),
        )
    )
    story.extend(
        [
            Spacer(1, 14 * mm),
            Paragraph("문서 활용 안내", styles["h2"]),
            Paragraph(
                "이 문서는 팀의 범위 합의, 역할 배정, Gate별 통과 판단과 발표 준비에 사용하는 실행 기준서다. "
                "법령 효력과 시행일은 구현 시점의 공식 API 응답으로 다시 검증한다.",
                styles["body"],
            ),
            PageBreak(),
        ]
    )

    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    story.extend(parse_markdown(lines, styles, content_width))
    doc.build(story)
    return OUTPUT


if __name__ == "__main__":
    print(build_pdf())
