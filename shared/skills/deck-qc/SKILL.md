---
name: deck-qc
description: Pitch deck and investor-presentation quality checker. Reviews a deck for (1) number consistency across slides, (2) data-narrative alignment, (3) language polish, (4) visual and formatting QC. Use whenever the user asks to review, check, QC, proof, or do a final pass on a deck or investor materials — including requests like "check my numbers", "reconcile figures across slides", "is this investor-ready", or "what am I missing before I send this out".
---

# Deck QC

Perform comprehensive QC on the presentation across four dimensions. Read every slide, then report findings.

## Environment check

This skill works headless (Python + python-pptx) or against a live deck. Identify which before starting:

- **Live deck** — read from the open file in PowerPoint or Google Slides if available.
- **Headless** — read from the uploaded `.pptx` file via python-pptx.

This is read-and-report only — no edits.

## Workflow

### Read the deck

Pull text from every slide, keeping slide-level attribution for every finding ("$500K appears on slides 3 and 8, but slide 15 shows $485K"). A 30-slide deck is too much to hold in working memory reliably — write the extracted text to a file so the number-checking script can process it.

The script expects markdown-ish input with slide markers:

```
## Slide 1
[slide 1 text content]

## Slide 2
[slide 2 text content]
```

### 1. Number consistency

Run the extraction script on what you collected:

```bash
python scripts/extract_numbers.py /tmp/deck_content.md --check
```

It normalizes units ($500K vs $500,000 → same number), categorizes values (revenue, MRR, ARR, downloads, multiples, margins), and flags when the same metric shows conflicting values on different slides. This is the part most likely to catch something a human missed on the fifth read-through.

Beyond what the script flags, verify:
- Calculations are correct (totals sum, percentages add up, growth rates match the endpoints)
- Unit style is consistent — pick one of $K or $M and stick with it
- Time periods are aligned — FY vs trailing-12 vs quarterly, explicitly labeled

### 2. Data-narrative alignment

Map claims to the data that's supposed to support them. This is where decks go wrong quietly — someone edits the chart on slide 7 and forgets the narrative on slide 4.

- Trend statements ("growing retention") → does the chart actually go that direction?
- Market position claims ("#1 in category") → downloads and share data support it?
- Plausibility — "#1 in a $100B market" with $200K revenue is 0.0002% share; that's not #1
- Overclaims — "patented" when only patent-pending; "clinically validated" when only pilot-stage. Flag every claim that lacks a citation.

### 3. Language polish

Investor decks have a register. Scan for anything that breaks it: casual phrasing ("pretty good", "a lot of"), contractions, exclamation points, vague quantifiers without numbers, inconsistent terminology for the same concept.

See `references/ib-terminology.md` for replacement patterns (originally IB-flavoured, the polish rules generalize).

### 4. Visual and formatting QC

Run standard visual verification checks on each slide. You're looking for: missing chart source citations, missing axis labels, typography inconsistencies, number formatting drift (1,000 vs 1K within the same deck), date format drift, footnote and disclaimer gaps.

Visual verification catches overlaps, overflow, and contrast issues that don't show up in text extraction. Don't skip it — a chart with no source citation looks the same as a properly sourced one in the text dump.

## Output

Use `references/report-format.md` as the structure. Categorize by severity:

- **Critical** — number mismatches, factual errors, data contradicting narrative, overclaims (patented vs pending). These block investor delivery.
- **Important** — language, missing sources, terminology drift. Should fix.
- **Minor** — font sizes, spacing, date formats. Polish.

Lead with criticals. If there aren't any, say so explicitly — "no number inconsistencies found" is a finding, not an absence of one.
