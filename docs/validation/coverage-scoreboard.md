# Coverage scoreboard

How much of a corpus of Word product information the certified-Word engine converts, measured the
same way at every release, and what got worse since the last one. The tool is
`zone-a/scripts/scoreboard.py` (`coverage` and `regress`). It is a measurement: nothing it writes
is a reading of a document, and it never prints or writes a label's text or file name.

## Running it

Run it at each release, from `zone-a/`, with at most two workers (`--jobs`, default 2):

```sh
nice -n 10 uv run --frozen python scripts/scoreboard.py coverage \
  --smpc CORPUS/en-smpc --pl CORPUS/en-pl --out OUT/<release> [--root REPO]
nice -n 10 uv run --frozen python scripts/scoreboard.py regress OUT/<previous> OUT/<release>
nice -n 10 uv run --frozen python scripts/scoreboard.py coverage \
  --smpc CORPUS/holdout/smpc --pl CORPUS/holdout/pl --holdout CORPUS/holdout/manifest.json \
  --out OUT/<release>-holdout
```

`CORPUS` is a folder outside the repository: the EMA's tracked-changes product information, in
English, cut into SmPCs (`cut_smpc.py`, everything before `ANNEX II`) and leaflets (`cut_pl.py`,
everything after `B. PACKAGE LEAFLET`). `--root` is the checkout whose registry, mapping and commit
are recorded (default: this one). `regress` exits 1 when anything got worse. `OUT` is never
committed. Only `summary.json` may be, under `docs/validation/coverage/` as
`<date>-main-<sha>.json`. The first is `coverage/2026-10-10-main-e546c933.json`.

## What it writes

`sections.jsonl` has one record per file, then one per section that was built, sorted by document
and the file's sha256. A record never carries the file's name. A refused section lists its
refusal `code` and `blockers`: every cause the builder's checks find, as code and detail, with
quoted values, code points and numbers taken out. A carried section has `contentSha256`, a hash
of its narrative and page.

`summary.json` holds aggregates only, for `smpc`, `pl` and `all`:

- `buckets`: each file in one bucket.
  - `whole`: built, every section carried.
  - `built-section-refused`: built, at least one section refused.
  - `document-refused`: the builder refused the whole document.
  - `needs-a-person`: a required section is missing, duplicated, out of order or has no code.
  - `parts-unclear`: an Annex I's SmPCs, or the leaflets, cannot be told apart.
  - `reader-refused`: the label reader refused the file.
- `sections`: sections `carried` out of `total`, and the `share`. The sections are those of built
  files only. Reader-refused files, and every other file that was not built, are not in it, so
  the share says how good the builder is on what reaches it. It does not say how much of the
  corpus converts.
- `characters`: the corpus-wide figure. `converted` out of `total` characters over all files:
  - A read file counts its characters as the reader reads them (each body paragraph's text, a
    tracked file by its accepted view). Its converted characters are those of the headings and
    paragraphs of its carried sections, each paragraph once.
  - A reader-refused file counts 0 converted characters. Its total is a plain-text extraction
    instead: the `w:t` text of `word/document.xml`, which leaves deletions out.
  - The two counts agree: over the 348 files of the dev corpus the reader reads, the plain-text
    extraction gives 0.999 of the reader's characters, both for SmPCs and for leaflets
    (2026-10-10).
  - Text before a document's first section heading is never converted, so even a `whole` file is
    a little under 100%.
- `codes`: the most frequent codes.
  - `reader`: reader refusals.
  - `document`: builder refusals of whole documents.
  - `needs`: template keys a person must settle.
  - `section`: section refusals.
- `unlock`: a greedy order over the `built-section-refused` files. Each step names the cause
  whose lifting next makes the most files whole, with the running number of whole files. Only
  the builder's causes are counted. It is optimistic: when one check in a paragraph refuses, the
  checks after it do not run. Neither do the narrative's and the table's checks unless they are
  the section's own refusal. `label-docx-reader/scripts/survey.py --causes` ranks the reader's
  causes the same way.
- `versions`: the reader, structurers, builder and fidelity norm, and the commit measured (with
  `-dirty` where the checkout had changes).

The same input gives the same bytes. The tool does not draw the documents: "carried" means the
builder carried the section, not that Chrome's drawing was compared (`scoreboard.py FOLDER`
without `--no-drawing` does that).

`regress BEFORE AFTER` matches records by sha256. It lists:

- each file whose bucket is worse (in the order above), or that stayed in its bucket with fewer
  sections carried, or that is gone;
- each section carried before that is now refused, changed (another `contentSha256`) or gone.

A file whose bytes changed is a new file to it.

## The dev corpus and the hold-out

The dev corpus (`en-smpc`, `en-pl`: the EMA's tracked-changes product information as fetched on
2026-10-05) is the tuning corpus. Rules have been written while looking at it, so its numbers are
optimistic.

The hold-out estimates what a new label meets. It holds the EMA's English tracked-changes product
information published or updated after 2026-10-09. `fetch-holdout.py` in the corpus folder
downloads it from the EMA's documents JSON report, waiting 8 s between requests. It writes
`holdout/manifest.json`: per file, its sha256, product, date and whether the product is new or an
update of one in the dev corpus.

Leak control:

- A person writing or tuning rules sees only the hold-out's aggregates. `--holdout` writes no
  `sections.jsonl` and no unlock order, only the `summary.json` split into `new`, `update` and
  `all`.
- Nobody opens, cuts by hand or debugs a hold-out file. A failure on it is fixed by finding the
  same cause in the dev corpus.
- With a few files, an aggregate is close to a per-file result. Read it as a trend, not as a
  target.
- When the hold-out has been used to decide a rule, it is spent. Move its files into the dev
  corpus and set a new cut-off date.
