#!/usr/bin/env python3
"""
section_paragraph_checker.py

QC check for PDF mystery-shop survey reports: every "Comments" answer must be
written as ONE paragraph. For each Comments field the script reports how many
paragraphs it found, flags any with more than one, and writes EVERY comment as
a single paragraph to <report>_fixed_comments.txt (merged ones are marked), so
all the comment text can be edited in one document and pasted back.

PDFs don't store paragraphs, only positioned lines of text, so breaks are
inferred from layout. A new paragraph is counted when a line:
  - has extra vertical space above it (a blank-line gap),
  - is indented compared to the comment's left edge, or
  - follows a line that stopped short even though this line's first word
    would have fit (a hard line break / Enter key).

Usage:
  python section_paragraph_checker.py                  # opens a file picker
  python section_paragraph_checker.py report.pdf
  python section_paragraph_checker.py reports_folder/  # every PDF in the folder
  python section_paragraph_checker.py report.pdf --debug   # show line-by-line detail
  python section_paragraph_checker.py report.pdf --label "Comments|Notes"

Exit codes: 0 = all comments pass, 1 = violations found, 2 = usage/file error.
Requires: pip install pymupdf
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

# Tuning knobs (points; 72 points = 1 inch)
GAP_FACTOR = 1.45           # line spacing above this x normal spacing = paragraph gap
INDENT_MIN = 8.0            # first-line indent that signals a new paragraph
FIT_SLACK = 4.0             # tolerance when testing whether the next word would fit
HEADING_SIZE_BUMP = 1.0     # text this much larger than body size counts as a heading
HEADING_MAX_CHARS = 120     # bold lines longer than this are body text, not headings
LABEL_MAX_CHARS = 70        # short lines ending in ":" are field labels (end a comment)
EDGE_MARGIN = 0.06          # top/bottom fraction of a page checked for headers/footers
PREVIEW_CHARS = 70

DEFAULT_LABEL = r"comments?"
BOLD_FLAG = 1 << 4
PAGE_NUM_RX = re.compile(r"^\s*(page\s*)?\d+(\s*(of|/)\s*\d+)?\s*$", re.IGNORECASE)
INSTRUCTION_RX = re.compile(r"^\(?\s*please\b", re.IGNORECASE)


@dataclass
class Line:
    text: str
    page: int          # 1-based
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool
    page_height: float

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class Comment:
    label: str
    page: int
    lines: list[Line] = field(default_factory=list)
    paragraphs: list[list[Line]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def violates(self) -> bool:
        return len(self.paragraphs) > 1


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def extract_lines(doc) -> list[Line]:
    lines: list[Line] = []
    for pno, page in enumerate(doc, start=1):
        data = page.get_text("dict", sort=True)
        for block in data["blocks"]:
            if block.get("type") != 0:
                continue
            for ln in block["lines"]:
                spans = [s for s in ln["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = "".join(s["text"] for s in ln["spans"]).strip()
                bold = all(
                    (s["flags"] & BOLD_FLAG)
                    or any(w in s["font"].lower() for w in ("bold", "black", "heavy"))
                    for s in spans
                )
                lines.append(Line(
                    text, pno,
                    min(s["bbox"][0] for s in spans), min(s["bbox"][1] for s in spans),
                    max(s["bbox"][2] for s in spans), max(s["bbox"][3] for s in spans),
                    max(s["size"] for s in spans), bold, page.rect.height,
                ))
    return merge_same_baseline(lines)


def merge_same_baseline(lines: list[Line]) -> list[Line]:
    """Join fragments split on one visual line (e.g. a bold word mid-sentence)."""
    lines.sort(key=lambda l: (l.page, round(l.y1, 0), l.x0))
    merged: list[Line] = []
    for ln in lines:
        prev = merged[-1] if merged else None
        if (
            prev and prev.page == ln.page
            and abs(prev.y1 - ln.y1) < 2.0
            and 0 <= ln.x0 - prev.x1 < 3 * prev.size
        ):
            prev.text = f"{prev.text} {ln.text}"
            prev.x1 = max(prev.x1, ln.x1)
            prev.y0 = min(prev.y0, ln.y0)
            prev.size = max(prev.size, ln.size)
            prev.bold = prev.bold and ln.bold
        else:
            merged.append(ln)
    return merged


def drop_headers_footers(lines: list[Line], page_count: int) -> list[Line]:
    """Remove page numbers and text repeated at the top/bottom of several pages."""
    def in_edge(l: Line) -> bool:
        return (l.y1 < l.page_height * EDGE_MARGIN * 2
                or l.y0 > l.page_height * (1 - EDGE_MARGIN * 2))

    def norm(t: str) -> str:
        return re.sub(r"\d+", "#", t.lower()).strip()

    repeated: set[str] = set()
    if page_count > 1:
        pages_with = defaultdict(set)
        for l in lines:
            if in_edge(l):
                pages_with[norm(l.text)].add(l.page)
        repeated = {k for k, v in pages_with.items() if len(v) >= 2}

    return [l for l in lines
            if not (in_edge(l) and (PAGE_NUM_RX.match(l.text) or norm(l.text) in repeated))]


# --------------------------------------------------------------------------- #
# Finding comment fields
# --------------------------------------------------------------------------- #
def body_font_size(lines: list[Line]) -> float:
    weights = Counter()
    for l in lines:
        weights[round(l.size, 1)] += len(l.text)
    return weights.most_common(1)[0][0] if weights else 10.0


def find_comments(lines: list[Line], label_rx: re.Pattern, body_size: float) -> list[Comment]:
    """A comment starts at a short line whose text ends with the label word (e.g.
    'Rewards Club Comments:') and runs until the next heading, field label, or
    form instruction."""
    def is_label(l: Line) -> bool:
        return len(l.text) <= 100 and bool(label_rx.search(l.text))

    def ends_comment(l: Line) -> bool:
        t = l.text
        return (
            (l.bold and len(t) <= HEADING_MAX_CHARS)
            or l.size >= body_size + HEADING_SIZE_BUMP
            or (t.endswith(":") and len(t) <= LABEL_MAX_CHARS)
            or bool(INSTRUCTION_RX.match(t))
        )

    comments: list[Comment] = []
    current: Comment | None = None
    for l in lines:
        if is_label(l):
            current = Comment(label=l.text.rstrip(":").strip(), page=l.page)
            comments.append(current)
            continue
        if current is None:
            continue
        if ends_comment(l):
            current = None
            continue
        current.lines.append(l)
    return comments


# --------------------------------------------------------------------------- #
# Paragraph inference
# --------------------------------------------------------------------------- #
def typical_pitch(lines: list[Line]) -> float:
    """Normal top-to-top distance between consecutive lines of one paragraph."""
    pitches = sorted(
        b.y0 - a.y0 for a, b in zip(lines, lines[1:])
        if a.page == b.page and 0 < b.y0 - a.y0 < 4 * a.height
    )
    if not pitches:
        return 0.0
    # The lower half is ordinary line spacing; paragraph gaps sit in the upper tail.
    return statistics.median(pitches[: max(1, (len(pitches) + 1) // 2)])


def first_word_width(line: Line) -> float:
    words = line.text.split()
    word = words[0] if words else ""
    avg_char = (line.x1 - line.x0) / max(1, len(line.text))
    return (len(word) + 1) * avg_char  # +1 for the space before it


def column_right(comments: list[Comment]) -> float:
    """Right edge of the comment text column, measured across the whole report so
    that comments made of only short lines can still be checked."""
    edges = sorted(l.x1 for c in comments for l in c.lines)
    if not edges:
        return 0.0
    return edges[int(0.98 * (len(edges) - 1))]  # ignore a rare overhanging line


def split_paragraphs(c: Comment, pitch: float, right_edge: float) -> None:
    body = c.lines
    if not body:
        return
    left = min(l.x0 for l in body)
    right_by_page = defaultdict(lambda: right_edge)
    for l in body:
        right_by_page[l.page] = max(right_by_page[l.page], l.x1)

    c.paragraphs, c.reasons = [[body[0]]], ["start"]
    for prev, cur in zip(body, body[1:]):
        reason = None
        if prev.page == cur.page and pitch and (cur.y0 - prev.y0) > GAP_FACTOR * pitch:
            reason = "blank-line gap"
        elif cur.x0 - left > INDENT_MIN and prev.x0 - left <= INDENT_MIN:
            reason = "indented first line"
        elif right_by_page[prev.page] - prev.x1 > first_word_width(cur) + FIT_SLACK:
            reason = "line break (previous line ended early)"
        if reason:
            c.paragraphs.append([cur])
            c.reasons.append(reason)
        else:
            c.paragraphs[-1].append(cur)


def join_lines(lines: list[Line]) -> str:
    """Rebuild running text from wrapped lines. A line ending in '-' was broken at a
    hyphenated word (e.g. 'table-' + 'games'), so no space is added there."""
    out = ""
    for l in lines:
        t = l.text.strip()
        if not out:
            out = t
        elif out.endswith("-") and not out.endswith(" -"):
            out += t
        else:
            out += " " + t
    return re.sub(r"\s{2,}", " ", out)


def merged_text(c: Comment) -> str:
    return " ".join(join_lines(p) for p in c.paragraphs)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def preview(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= PREVIEW_CHARS else text[: PREVIEW_CHARS - 1] + "…"


class CheckError(Exception):
    """A report could not be checked (unreadable, no text, or no comment fields)."""


def label_pattern(label: str = DEFAULT_LABEL) -> re.Pattern:
    return re.compile(rf"\b(?:{label})\s*:?\s*$", re.IGNORECASE)


def analyze_pdf(path, label_rx: re.Pattern | None = None) -> tuple[list[Comment], float]:
    """Find every comment field in a PDF and split it into inferred paragraphs.

    Returns (comments, normal_line_pitch). Raises CheckError if the PDF can't be
    checked."""
    label_rx = label_rx or label_pattern()
    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:  # noqa: BLE001
        raise CheckError(f"could not open: {exc}") from exc

    lines = extract_lines(doc)
    if not lines:
        raise CheckError("No text found. This PDF may be a scanned image; it needs OCR first.")
    lines = drop_headers_footers(lines, doc.page_count)

    comments = find_comments(lines, label_rx, body_font_size(lines))
    if not comments:
        raise CheckError("No Comments fields found. If the label is worded differently, use --label.")

    pitch = typical_pitch([l for c in comments for l in c.lines])
    right_edge = column_right(comments)
    for c in comments:
        split_paragraphs(c, pitch, right_edge)
    return comments, pitch


def write_comments_file(path: Path, comments: list[Comment]) -> Path:
    """Write every non-blank comment, in report order, as one paragraph each.

    Each comment's header says whether it was merged from several paragraphs
    (check the flow) or was already a single paragraph (text unchanged)."""
    merged_count = sum(c.violates for c in comments)
    out = path.with_name(f"{path.stem}_fixed_comments.txt")
    with out.open("w", encoding="utf-8") as fh:
        fh.write(f"All comments from {path.name}, each as one paragraph\n")
        fh.write(f"{len(comments)} comment(s); {merged_count} merged from multiple paragraphs.\n")
        fh.write("Edit freely here, then paste each one over the original comment in the survey.\n")
        fh.write("For MERGED comments: where a new paragraph used to begin, a transition word\n")
        fh.write("may help the sentences flow together.\n")
        for c in comments:
            status = (f"MERGED from {len(c.paragraphs)} paragraphs - check the flow"
                      if c.violates else "already one paragraph - unchanged")
            fh.write(f"\n{'=' * 70}\n{c.label}  (page {c.page})\n{status}\n{'=' * 70}\n")
            fh.write(merged_text(c) + "\n")
    return out


def check_file(path: Path, args, label_rx: re.Pattern) -> int:
    print(f"\n{path.name}")
    print("-" * min(80, max(20, len(path.name))))
    try:
        comments, pitch = analyze_pdf(path, label_rx)
    except CheckError as exc:
        print(f"  {exc}")
        return -1

    filled = [c for c in comments if c.lines]
    violations = [c for c in filled if c.violates]
    for c in filled:
        n = len(c.paragraphs)
        status = "FAIL" if c.violates else "ok  "
        print(f"  [{status}] p.{c.page}  {c.label}  ({n} paragraph{'s' if n != 1 else ''})")
        if c.violates or args.debug:
            for i, (para, why) in enumerate(zip(c.paragraphs, c.reasons), 1):
                tag = f"   <- {why}" if i > 1 else ""
                print(f"           {i}. p.{para[0].page}: {preview(para[0].text)}{tag}")
        if args.debug:
            for l in c.lines:
                print(f"              | p{l.page} x={l.x0:6.1f}-{l.x1:6.1f} y={l.y0:6.1f} "
                      f"size={l.size:4.1f}{' B' if l.bold else '  '} {preview(l.text)}")

    blank = len(comments) - len(filled)
    print(f"\n  {len(filled)} comment(s) checked, {len(violations)} with multiple paragraphs"
          + (f" ({blank} blank comment field(s) skipped)." if blank else "."))

    if filled:
        out = write_comments_file(path, filled)
        print(f"  All {len(filled)} comment(s) saved as single paragraphs to: {out}")
    if args.debug:
        print(f"  (normal line spacing in comments: {pitch:.1f}pt)")
    return len(violations)


def collect_files(target: Path) -> list[Path]:
    if target.is_dir():
        return sorted(p for p in target.iterdir() if p.suffix.lower() == ".pdf")
    return [target]


def pick_target() -> Path | None:
    """No path given (e.g. PyCharm's Run button): open a file picker, else ask in the console."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askopenfilename(
            title="Choose a survey report to check",
            initialdir=str(Path.home() / "Downloads"),
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        root.destroy()
        return Path(chosen) if chosen else None
    except Exception:  # noqa: BLE001  (tkinter missing or no display)
        typed = input("Drag a PDF (or folder) here, or paste its path, then press Enter:\n> ")
        typed = typed.strip().strip("'\"")
        return Path(typed).expanduser() if typed else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Flag survey Comments answers written as more than one paragraph."
    )
    ap.add_argument("target", type=Path, nargs="?",
                    help="PDF file or folder of PDFs (omit to pick one)")
    ap.add_argument("--label", default=DEFAULT_LABEL,
                    help="word(s) that end a comment field's label, as a regex "
                         "(default: 'comments?')")
    ap.add_argument("--debug", action="store_true",
                    help="show every line's position and why each paragraph break was counted")
    args = ap.parse_args(argv)

    label_rx = label_pattern(args.label)

    if args.target is None:
        args.target = pick_target()
        if args.target is None:
            print("No file chosen.")
            return 2
    if not args.target.exists():
        print(f"Not found: {args.target}")
        return 2

    files = collect_files(args.target)
    if not files:
        print(f"No PDF files found in {args.target}")
        return 2

    total = errors = 0
    for f in files:
        result = check_file(f, args, label_rx)
        if result < 0:
            errors += 1
        else:
            total += result

    if len(files) > 1:
        print(f"\nSummary: {len(files)} file(s), {total} comment(s) with multiple paragraphs, "
              f"{errors} file(s) could not be checked.")

    if errors and not total:
        return 2
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
