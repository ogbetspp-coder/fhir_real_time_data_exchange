# Word SmPCs

Five SmPCs as Microsoft Word writes them, for `tests/test_word_epi.py`: Word 16.113.3 (macOS)
opened each EMA ePI pinned in `labels/ema-epi/sources/`, written as HTML (ANNEX I, each section's
title as a paragraph and its narrative, ANNEX II), and saved it as a .docx
(`scripts/word_fixtures.py make`). The text is the EMA's, public; the files are Word's own, with
its lists, tables, styles and marks, as a company's label is.

Not the EMA's text in every place:

- pictures and rules (`img`, `hr`) are left out, since Word imports them as fields and drawings
  the reader refuses;
- the Jentadueto ePI writes a bare "<" in three places ("GFR < 30", "pH (< 7.35)", "≥ 10 to < 18
  years"), and Word's HTML import reads each as the start of a tag and drops the text up to the
  next ">": the .docx lacks those words.

Each carried section's text was compared with the EMA's as the label reader reads it, line by
line: equal, but for those two differences and the sections the EMA nests otherwise than the
template.

`chrome.json` is what Google Chrome 154.0.8037.93 drew for each file's carried narratives
(`scripts/word_fixtures.py record`); the tests replay it where Chrome is not installed, and run
Chrome itself where it is.

| File                                 | SHA-256                                                            |
| ------------------------------------ | ------------------------------------------------------------------ |
| `brukinsa-smpc-en.docx`              | `808d5f5dd6b135411cb2e307b990b3406a9a27653a2b9d089d015636155c15ae` |
| `imatinib-teva-smpc-en.docx`         | `b2f172241b5780b6034584985242bdc8157c62c55de8b400ff4e5120e49a5870` |
| `imatinib-teva-tablets-smpc-en.docx` | `c0abb3aac7d75c5129e34f8b17a4cf504cf609dcc2b2321e28dae34dbb9a3fb5` |
| `jentadueto-smpc-en.docx`            | `3d80ca6fc043f8a3bec677c89d7fcbbe82316a95c1f318d546defab583a0a2ab` |
| `nuvaxovid-smpc-en.docx`             | `c8e2a8e75a6a6fc2a9eb9de688f5e814ddc680eccb21868f24cf461c4d934e2b` |
