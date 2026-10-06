# Scoped refusals: an object or a paragraph the reader cannot read, not a document

- Status: phase 1 implemented, 2026-10-05 (`docx-reader/1.30.0`, `label-docx-json/1.18.0`,
  `conservation-check/1.18.0`, `word-epi/1.1.0`); phase 2 proposed. Second draft, after one
  independent review
- Decides: how the label reader (`label-docx-reader/`) reports what it cannot read exactly when
  that thing is confined to one object anchored in a paragraph (phase 1), and the conditions under
  which a paragraph may later be scoped (phase 2)
- Related: ADR 0006 (P1: the reader reports what the ePI builder needs), the reader's
  `docs/conservation.md`, `zone-a/src/zone_a/word_epi.py`

## Why

The reader refuses a whole document for anything it cannot read exactly. On 187 real
company-written SmPCs (the EMA's published product information with tracked changes, cut at
ANNEX II) it read 54. Of the 133 it refused, 61 were refused first for something confined to one
paragraph or one drawn object, 23 of them a figure built from text boxes (mostly a Kaplan-Meier
curve or a concentration plot in section 5.1). One such figure keeps the other thirty sections from
being read, and the label team learns only that the document failed, not where. The 61 count only
first refusals: a document may hold more than one.

## Review findings that shape this draft

The first review showed that scoping a paragraph is not local in general. Footnote and endnote
numbers, SEQ counters, list counters, bookmarks and the REF, NOTEREF, PAGEREF and STYLEREF fields
computed from them all cross paragraphs; a refusal raised mid-paragraph stops the walk before them,
so a later number would silently shift, in the reader and the check alike. One refusal code can be
local in one place and global in another (`hidden-text` for a hidden run, a hidden paragraph mark or
a hidden note mark; `computed-field` for a DATE field or `updateFields`). The conservation check
cannot independently decide every scope: it has no layout, colour or field rules, so it can confirm
that what it cannot read is marked, not why. And consumers fail open on an empty paragraph
(`zone_a.implementation` and the registry read one as layout). So scoping starts with the one unit
that is closed, an anchored object, and paragraphs wait for phase 2.

## Phase 1: anchored objects

1. **What is scoped.** A floating object anchored in a paragraph (`wp:anchor`, or VML positioned
   off the text flow) that the reader refuses today only because it holds text: a text box, a
   group or canvas of shapes and text boxes, in `mc:AlternateContent` or VML. Its text is not in
   the paragraph's flow, as Word does not draw it there.
2. **The condition.** Nothing inside the object is referred to or counted elsewhere: no field, no
   footnote or endnote reference, no list item, no bookmark, no comment range, no tracked change,
   no SEQ. An object holding any of them is still refused whole, as today.
3. **What the reader reports.** The anchor paragraph is read as today. It gains an `anchored`
   entry for the object: its offset in the paragraph (where its anchor stands), its kind
   (`text-box`, `shapes`), and `read: false`. Floating pictures and shapes without text, which
   the reader sets aside and counts today, get the same entry with `read: false` too, so a
   consumer learns where each floating object stands.
4. **The certificate.** `setAside` counts the object's characters (`unreadObjects`, its text
   characters), by the check's own walk, both the `mc:Choice` and the `mc:Fallback`.
5. **The conservation check** finds every anchored object by its own walk, applies its own copy
   of the condition (2), requires the reader's `anchored` entries to equal its own, and refuses a
   result whose anchor paragraph text includes any of an object's text. As built, the last is the
   text's equality: the paragraph's text must be exactly its own tokens, so any of an object's
   text put there is refused, while a substring rule would refuse a label that also stands in its
   paragraph (a text box paragraph's text stands in its anchor paragraph for 6 of 3,764 text
   objects in the EMA's files, 4 of them three characters or fewer).
6. **The outcome** is `read` (the body's text is whole: the object is not in it, as in Word), with
   the anchored objects counted in the result. No outcome or exit code changes meaning.
7. **Downstream.** `zone_a.word_epi` refuses the section whose paragraph anchors an object
   (`anchored-object`), instead of the whole document (today's `floating-object` from the
   certificate's count); `zone_a.drawing` and the page are unchanged. `zone_a.implementation`,
   the registry and the structurer are unaffected (no paragraph changes).

## Phase 2: paragraphs, later, under these conditions

Recorded here so phase 1 does not foreclose them; not designed in full yet:

- **Record, do not raise:** a refusal that can be scoped is recorded and the structural walk goes
  on (notes, fields, bookmarks, SEQ, section breaks), so counters stay exact.
- **Scope by raise site,** in a closed list; anything not on it refuses the document.
- **Dependents:** a paragraph whose computed text reads an unread paragraph (REF, STYLEREF,
  NOTEREF, PAGEREF) is unread too, transitively.
- **Fail closed:** an unread paragraph's `text` is null (not empty), the format's major version
  moves, and `zone_a.certified.read_body` refuses a result with an unread paragraph unless a caller
  opts in; the receipt, the stored outcome and the Word verdict count unread paragraphs.
- **A label-unread state** for a list label the reader cannot draw while the text is readable.
- **Units other than a paragraph:** a cell (a row too low for its text), a frame (`framePr`
  groups paragraphs), a table (its style), a block `sdt`.

## Proof required for phase 1

- The snapshot outputs: every output read today unchanged but for the new `anchored` entries of
  floating pictures and shapes; every output whose outcome changes goes from refused (`AlternateContent
that can hold text` or its VML form) to read, with each such object reported.
- A synthetic document per object kind and per condition (an object holding a field, a note
  reference, a list item, a bookmark: still refused).
- Tampering: an `anchored` entry added, dropped, moved or marked read; an object's text added to
  its paragraph.
- The mutation record holds the new rules.
- The EMA corpus survey before and after (counts and codes only).
