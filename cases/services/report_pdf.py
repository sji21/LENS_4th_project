from __future__ import annotations

import html
import os
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate
from django.utils import timezone


GREEN = colors.HexColor("#24685C")
INK = colors.HexColor("#233C39")
MUTED = colors.HexColor("#75817C")
LINE = colors.HexColor("#DDE5DC")
PALE = colors.HexColor("#F4F7F1")
FONT_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf"),
)


def _font_name():
    configured = os.getenv("KOREAN_FONT_PATH", "").strip()
    candidates = ((Path(configured).expanduser(),) + FONT_CANDIDATES) if configured else FONT_CANDIDATES
    font_path = next((path for path in candidates if path.is_file()), None)
    if font_path:
        name = "LensKoreanReport"
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(font_path)))
        return name
    name = "HYSMyeongJo-Medium"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(name))
    return name


def _safe(value):
    text = str(value if value is not None else "").replace("\u00a0", " ")
    text = text.replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-")
    return html.escape(text.strip()).replace("\n", "<br/>")


def _styles(font):
    base = getSampleStyleSheet()
    return {
        "kicker": ParagraphStyle("ReportKicker", parent=base["BodyText"], fontName=font, fontSize=8,
                                 leading=11, textColor=GREEN, spaceAfter=5),
        "title": ParagraphStyle("ReportTitle", parent=base["Title"], fontName=font, fontSize=21,
                                leading=29, textColor=INK, spaceAfter=7),
        "meta": ParagraphStyle("ReportMeta", parent=base["BodyText"], fontName=font, fontSize=8.5,
                               leading=13, textColor=MUTED, spaceAfter=15),
        "heading": ParagraphStyle("ReportHeading", parent=base["Heading2"], fontName=font, fontSize=12,
                                  leading=17, textColor=GREEN, spaceBefore=12, spaceAfter=7,
                                  keepWithNext=True),
        "body": ParagraphStyle("ReportBody", parent=base["BodyText"], fontName=font, fontSize=9.3,
                               leading=15, textColor=INK, spaceAfter=5, wordWrap="CJK"),
        "bullet": ParagraphStyle("ReportBullet", parent=base["BodyText"], fontName=font, fontSize=9.3,
                                 leading=15, leftIndent=11, firstLineIndent=-8, textColor=INK,
                                 spaceAfter=5, wordWrap="CJK"),
        "note": ParagraphStyle("ReportNote", parent=base["BodyText"], fontName=font, fontSize=8,
                               leading=13, textColor=MUTED, backColor=PALE, borderPadding=9,
                               spaceBefore=14),
        "empty": ParagraphStyle("ReportEmpty", parent=base["BodyText"], fontName=font, fontSize=9,
                                leading=14, textColor=MUTED, spaceAfter=4),
        "footer": ParagraphStyle("ReportFooter", parent=base["BodyText"], fontName=font, fontSize=7.5,
                                 leading=10, textColor=MUTED, alignment=TA_CENTER),
    }


def _add_section(story, styles, title, values, empty_message):
    story.append(Paragraph(_safe(title), styles["heading"]))
    if isinstance(values, (list, tuple)):
        if values:
            story.extend(Paragraph(f"- {_safe(value)}", styles["bullet"]) for value in values)
        else:
            story.append(Paragraph(_safe(empty_message), styles["empty"]))
    elif values:
        story.append(Paragraph(_safe(values), styles["body"]))
    else:
        story.append(Paragraph(_safe(empty_message), styles["empty"]))


def render_report_pdf(report):
    """Render the saved report snapshot as downloadable PDF bytes."""
    buffer = BytesIO()
    font = _font_name()
    styles = _styles(font)
    document = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=19 * mm, leftMargin=19 * mm,
        topMargin=18 * mm, bottomMargin=17 * mm,
        title=f"LENS 상담 리포트 v{report.version}", author="LENS",
    )
    content = report.content_json or {}
    case_title = str(report.case.title).strip()
    report_title = f"{case_title} 리포트" if case_title.endswith("상담") else f"{case_title} 상담 리포트"
    mode_label = "AI 요약" if report.generation_mode == "llm" else "기본 요약"
    created_at = timezone.localtime(report.created_at)
    story = [
        Paragraph("LENS · LEASE EVIDENCE NAVIGATION SYSTEM", styles["kicker"]),
        Paragraph(_safe(report_title), styles["title"]),
        Paragraph(
            f"버전 {report.version} · {created_at:%Y.%m.%d %H:%M} 생성 · {mode_label}",
            styles["meta"],
        ),
        HRFlowable(width="100%", thickness=.7, color=LINE, spaceAfter=5),
    ]
    _add_section(story, styles, "상담 요약", content.get("case_summary"), "정리된 요약이 없습니다.")
    _add_section(story, styles, "사용자가 궁금해한 내용", content.get("user_interests", []), "정리된 질문이 없습니다.")
    _add_section(story, styles, "주요 질문과 확인한 내용", content.get("questions_and_answers", []), "검증된 답변이 없습니다.")
    _add_section(story, styles, "확인된 내용", content.get("confirmed_items", []), "확인된 항목이 없습니다.")
    _add_section(story, styles, "미확인 내용", content.get("unresolved_items", []), "표시할 항목이 없습니다.")
    _add_section(story, styles, "추가로 알아볼 내용", content.get("next_checks", []), "추가 확인 항목이 없습니다.")
    _add_section(story, styles, "참고 근거", content.get("source_refs", []), "연결된 출처가 없습니다.")
    story.append(Paragraph(
        "이 리포트는 생성 시점까지 확인된 상담 기록을 정리한 자료입니다. 법률 자문이나 계약 안전 판정을 대신하지 않습니다.",
        styles["note"],
    ))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.line(19 * mm, 12 * mm, A4[0] - 19 * mm, 12 * mm)
        canvas.setFont(font, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawCentredString(A4[0] / 2, 7.5 * mm, f"LENS 상담 리포트 · {doc.page}쪽")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def report_filename(report):
    return f"LENS_report_{report.case_id}_v{report.version}.pdf"
