# Survey Paragraph Checker

A quality-control tool for PDF survey reports whose style guide requires every
free-text **Comments** answer to be written as a single paragraph. It flags any
comment that was split into multiple paragraphs and writes every comment, as a
single paragraph, to one text file. You can edit all the comment text in one
place, then paste it back into the survey system.

Built for mystery-shop evaluation reports, where each section ends with a
narrative comments field (for example, "Cage Cashier Comments:") surrounded by
scored Yes/No questions.

## Why this exists

Grammarly style guides and similar tools work on words and phrases, so they
can't enforce a structural rule like "one paragraph per section." PDFs also
don't store paragraphs. They only store positioned lines of text, so a simple
text search can't tell a wrapped line from a new paragraph. This tool reads the
page layout directly to make that call.

## How it works

1. **Extracts every line of text** with its position, font size, and weight
   using [PyMuPDF](https://pymupdf.readthedocs.io/), and drops repeated page
   headers, footers, and page numbers.
2. **Finds the Comments fields.** A comment starts at a label ending in
   "Comments" and runs until the next bold heading, field label (a short line
   ending in `:`), or form instruction such as "(Please upload a photo…)".
3. **Infers paragraph breaks** inside each comment. A new paragraph is counted
   when a line has:
   - **a blank-line gap** above it (spacing well beyond the report's normal
     line spacing),
   - **an indented first line**, or
   - **a hard line break**: the previous line stopped short even though this
     line's first word would have fit, which happens when someone presses Enter.
4. **Reports and fixes.** Each comment is marked `ok` or `FAIL`. Every
   non-blank comment is then written, in report order, to
   `<report>_fixed_comments.txt`. Failures are rejoined into one paragraph
   (hyphenated words split across lines are rejoined too) and marked
   **MERGED**. Comments that already pass are included unchanged, so the whole
   report's comment text can be edited in a single document.

### Example comments file

```
======================================================================
Rewards Club Comments  (page 3)
MERGED from 3 paragraphs - check the flow
======================================================================
At approximately 3:31 PM, I approached the Rewards Club desk and was greeted…

======================================================================
Swing Shift - Slot Comments  (page 4)
already one paragraph - unchanged
======================================================================
Upon entering the slot floor, I observed…
```

## Installation

Requires Python 3.9+.

```bash
git clone https://github.com/joolybugg/survey-paragraph-checker.git
cd survey-paragraph-checker
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install .
```

This installs the `paragraph-check` command. To run the script directly
instead, `pip install -r requirements.txt` is enough.

## Usage

```bash
paragraph-check                         # opens a file picker
paragraph-check report.pdf              # check one report
paragraph-check reports/                # check every PDF in a folder
paragraph-check report.pdf --debug      # show line positions and break reasons
paragraph-check report.pdf --label "comments?|notes"   # other field labels
```

From PyCharm or another IDE, run `section_paragraph_checker.py` with no
arguments and a file picker opens.

### Example output

```
evaluation_1234567.pdf
----------------------
  [FAIL] p.3  Rewards Club Comments  (3 paragraphs)
           1. p.3: At approximately 3:31 PM, I approached the Rewards Club desk…
           2. p.3: The associate explained that, as a new member, I would rece…   <- blank-line gap
           3. p.3: The associate remained patient, upbeat, and professional wh…   <- blank-line gap
  [ok  ] p.4  Swing Shift - Slot Comments  (1 paragraph)
  [FAIL] p.15 Casual Dining - Management Comments  (2 paragraphs)
           1. p.15: I did not have a clearly identifiable interaction with a ma…
           2. p.15: Although the issue was handled professionally, no manager …   <- line break (previous line ended early)

  3 comment(s) checked, 2 with multiple paragraphs.
  All 3 comment(s) saved as single paragraphs to: evaluation_1234567_fixed_comments.txt
```

Exit codes: `0` all comments pass, `1` at least one comment has multiple
paragraphs, `2` usage or file error. This makes it easy to use in batch
scripts.

## Tuning

The detection thresholds are constants at the top of
`section_paragraph_checker.py`:

| Setting | Default | What it controls |
| --- | --- | --- |
| `GAP_FACTOR` | 1.45 | Spacing (× normal line spacing) that counts as a blank-line gap |
| `INDENT_MIN` | 8 pt | First-line indent that signals a new paragraph |
| `FIT_SLACK` | 4 pt | Tolerance when testing whether the next word would have fit |
| `LABEL_MAX_CHARS` | 70 | Short lines ending in `:` are treated as field labels |

If results look off for a new report layout, run with `--debug` to see each
line's position and why each break was counted.

## Limitations

- Check-only for the PDF itself. Corrections are made in the source system,
  using the generated text file.
- Scanned (image-only) PDFs need OCR first.
- A comment made of a single line can't be split, so it always passes.

## Privacy

Survey reports often contain names, locations, and client details. The
`.gitignore` excludes `*.pdf` and `*_fixed_comments.txt` so real reports and
their output are never committed. The tests generate synthetic PDFs instead.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
