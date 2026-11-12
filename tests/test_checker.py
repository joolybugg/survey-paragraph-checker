"""Tests run against synthetic survey PDFs generated with reportlab, so no real
(client) reports are needed or stored in the repository."""

from __future__ import annotations

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table

import section_paragraph_checker as spc

HEADING = ParagraphStyle("H", fontName="Helvetica-Bold", fontSize=11, spaceBefore=12, spaceAfter=4)
QUESTION = ParagraphStyle("Q", fontName="Helvetica", fontSize=9, leading=11)
LABEL = ParagraphStyle("L", fontName="Helvetica", fontSize=9, leading=11, spaceBefore=6)
BODY = ParagraphStyle("C", fontName="Helvetica", fontSize=9, leading=11)
BODY_GAP = ParagraphStyle("Cg", parent=BODY, spaceBefore=11)
BODY_INDENT = ParagraphStyle("Ci", parent=BODY, firstLineIndent=24)

SENTENCES = (
    "At approximately 3:31 PM, I approached the service desk and was greeted by the "
    "associate, who smiled and made eye contact. She asked for my ID and explained the "
    "sign-up offer in detail while entering my information. "
)
OTHER = (
    "The associate explained that, as a new member, I would receive a bonus on my card "
    "after my first visit, and she thanked me warmly before I left the desk. "
)


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.drawString(72, 765, "Evaluation 0000000 - Synthetic Test Report")
    canvas.drawRightString(540, 25, f"Page {doc.page}")
    canvas.restoreState()


def questions(prefix: str, n: int) -> Table:
    rows = [
        [Paragraph(f"{prefix} - {i}. Did the Team Member acknowledge you in a timely and "
                   f"friendly manner when you arrived?", QUESTION), "3/3", "Yes"]
        for i in range(1, n + 1)
    ]
    return Table(rows, colWidths=[300, 50, 80])


def section(title: str, label: str, paragraphs: list[tuple[str, ParagraphStyle]], n_questions=2):
    story = [Paragraph(title, HEADING), questions(title.title(), n_questions),
             Paragraph(label, LABEL)]
    story += [Paragraph(text, style) for text, style in paragraphs]
    return story


def build(tmp_path, story, name="report.pdf"):
    path = tmp_path / name
    SimpleDocTemplate(str(path), pagesize=letter).build(story, onFirstPage=footer,
                                                       onLaterPages=footer)
    return path


def analyze(path):
    comments, _ = spc.analyze_pdf(path)
    return {c.label: c for c in comments}


# --------------------------------------------------------------------------- #

def test_single_paragraph_passes(tmp_path):
    path = build(tmp_path, section("SLOTS", "Slot Comments:", [(SENTENCES * 3, BODY)]))
    c = analyze(path)["Slot Comments"]
    assert len(c.paragraphs) == 1
    assert not c.violates


@pytest.mark.parametrize("style, reason", [
    (BODY_GAP, "blank-line gap"),
    (BODY_INDENT, "indented first line"),
])
def test_spaced_and_indented_paragraphs_are_flagged(tmp_path, style, reason):
    first_style = BODY_INDENT if style is BODY_INDENT else BODY
    path = build(tmp_path, section("CAGE", "Cage Comments:",
                                   [(SENTENCES * 2, first_style), (OTHER * 2, style)]))
    c = analyze(path)["Cage Comments"]
    assert len(c.paragraphs) == 2
    assert c.reasons[1] == reason


def test_hard_line_break_between_short_lines_is_flagged(tmp_path):
    story = section("SLOTS", "Slot Comments:", [(SENTENCES * 3, BODY)])  # sets column width
    story += section("MANAGEMENT", "Management Comments:", [
        ("I did not have a clearly identifiable interaction with a manager.", BODY),
        ("No manager visited the table during the meal.", BODY),
    ])
    c = analyze(build(tmp_path, story))["Management Comments"]
    assert len(c.paragraphs) == 2
    assert c.reasons[1].startswith("line break")


def test_paragraph_spanning_page_break_passes(tmp_path):
    story = section("TIMING", "Timing Comments:", [((SENTENCES + OTHER) * 12, BODY)],
                    n_questions=14)
    c = analyze(build(tmp_path, story))["Timing Comments"]
    assert {l.page for l in c.lines} == {1, 2}, "fixture should cross a page break"
    assert len(c.paragraphs) == 1


def test_comment_stops_at_next_label_and_instruction(tmp_path):
    story = section("FOOD", "Food Comments:", [(SENTENCES, BODY)])
    story += [Paragraph("Food Upload:", LABEL),
              Paragraph("(Please upload a photo of your meal.)", QUESTION)]
    c = analyze(build(tmp_path, story))["Food Comments"]
    assert "upload" not in spc.merged_text(c).lower()


def test_blank_comment_field_is_skipped(tmp_path):
    story = section("SLOTS", "Slot Comments:", [(SENTENCES, BODY)])
    story += [Paragraph("ROAMING SECURITY", HEADING), Paragraph("Roaming Comments:", LABEL)]
    comments = analyze(build(tmp_path, story))
    assert comments["Roaming Comments"].lines == []
    assert not comments["Roaming Comments"].violates


def test_merged_text_is_one_paragraph_with_all_words(tmp_path):
    story = section("CAGE", "Cage Comments:", [(SENTENCES * 2, BODY), (OTHER * 2, BODY_GAP)])
    c = analyze(build(tmp_path, story))["Cage Comments"]
    merged = spc.merged_text(c)
    assert "\n" not in merged
    assert merged.split() == (SENTENCES * 2 + OTHER * 2).split()


def test_join_lines_keeps_hyphenated_words_together():
    def line(text):
        return spc.Line(text, 1, 0, 0, 100, 10, 9, False, 792)
    assert spc.join_lines([line("the table-"), line("games area")]) == "the table-games area"
    assert spc.join_lines([line("cost -"), line("free")]) == "cost - free"


def test_cli_exit_codes_and_fixed_file(tmp_path, capsys):
    good = build(tmp_path, section("SLOTS", "Slot Comments:", [(SENTENCES, BODY)]), "good.pdf")
    bad = build(tmp_path, section("CAGE", "Cage Comments:",
                                  [(SENTENCES, BODY), (OTHER, BODY_GAP)]), "bad.pdf")

    assert spc.main([str(good)]) == 0
    assert not (tmp_path / "good_fixed_comments.txt").exists()

    assert spc.main([str(bad)]) == 1
    fixed = (tmp_path / "bad_fixed_comments.txt").read_text()
    assert "Cage Comments" in fixed and "was 2 paragraphs" in fixed

    assert spc.main([str(tmp_path / "missing.pdf")]) == 2
    assert "FAIL" in capsys.readouterr().out


def test_custom_label(tmp_path):
    story = section("NOTES", "Shopper Notes:", [(SENTENCES, BODY), (OTHER, BODY_GAP)])
    path = build(tmp_path, story)
    comments, _ = spc.analyze_pdf(path, spc.label_pattern("notes?"))
    assert comments[0].violates
