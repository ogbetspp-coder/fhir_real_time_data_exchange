"""Ask Microsoft Word for what it shows, and hold the reader to it (macOS only).

    uv run --frozen python scripts/word_oracle.py record corpus/numbering-cases
    uv run --frozen python scripts/word_oracle.py compare path/to/label.docx [...]
    uv run --frozen python scripts/word_oracle.py update FOLDER path/to/generated.docx [...]

The questions asked and how each answer is judged are ``label_docx.word``. ``record`` asks Word
about every .docx in a corpus set and writes the answers to the set's ``word.json``, which
``tests/test_word_oracle.py`` holds the reader to without Word. ``compare`` prints the verdict
for any files and writes nothing, so a confidential label can be checked on one machine and
never enter the repository. Both print, for each file, whether the reader agrees with Word,
differs (where), or refuses; never the paragraphs' text. ``update`` has Word update every field
of each file and save it into FOLDER, in Word's own XML: generated documents hold placeholder
field results, and Word's are the ones the reader must then compute.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from label_docx.word import (
    VERIFIER,
    ask,
    is_tracked,
    judge,
    judge_tracked,
    word_updated,
    word_version,
)


def main() -> int:
    """Record a corpus set's answers, or compare files.

    1 if the reader differs from Word, else 2 if Word judged not every file, else 0.
    """
    parser = argparse.ArgumentParser(description="Hold the reader's list labels to Word's.")
    commands = parser.add_subparsers(dest="command", required=True)
    recording = commands.add_parser("record", help="write word.json for a corpus set")
    recording.add_argument("folder", type=Path)
    recording.add_argument(
        "--only",
        nargs="+",
        default=[],
        help="ask Word about these files only, keeping the answers on record for the rest",
    )
    commands.add_parser("compare", help="compare files, writing nothing").add_argument(
        "files", type=Path, nargs="+"
    )
    updating = commands.add_parser("update", help="have Word update the fields, save copies")
    updating.add_argument("folder", type=Path)
    updating.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args()
    if args.command == "update":
        args.folder.mkdir(parents=True, exist_ok=True)
        for path in args.files:
            (args.folder / path.name).write_bytes(word_updated(path))
            sys.stdout.write(f"{path.name}: updated\n")
        return 0
    paths = sorted(args.folder.glob("*.docx")) if args.command == "record" else args.files
    if args.command == "record":
        unknown = sorted(set(args.only) - {path.name for path in paths})
        if unknown:
            raise SystemExit(f"--only names no .docx of {args.folder}: {', '.join(unknown)}")
    # An answer on record is reused only for the same bytes, Word version and questions.
    application = word_version() if args.command == "record" else ""
    answers: dict[str, list[str]] = {}
    at_answers: dict[str, list[int]] = {}
    digests: dict[str, str] = {}
    font_answers: dict[str, list[str | None]] = {}
    note_answers: dict[str, dict[str, list[str]]] = {}
    field_answers: dict[str, dict[str, list[str]]] = {}
    print_answers: dict[str, bool] = {}
    emphasis_answers: dict[str, dict[str, list[bool]]] = {}
    story_answers: dict[str, dict[str, list[list[Any]]]] = {}
    note_text_answers: dict[str, dict[str, list[str]]] = {}
    text_answers: dict[str, list[str]] = {}
    # A recording keeps each file's answers as it goes, so one stopped part way resumes there.
    progress = args.folder / ".word-progress.json" if args.command == "record" else None
    recorded: dict[str, dict[str, Any]] = (
        json.loads(progress.read_text("utf-8")) if progress and progress.exists() else {}
    )
    if args.command == "record" and args.only:
        kept = json.loads((args.folder / "word.json").read_text("utf-8"))
        for name in kept["drawn"]:
            if name not in args.only and name in kept.get("sha256", {}):
                recorded.setdefault(
                    name,
                    {
                        "drawn": kept["drawn"][name],
                        "fonts": kept["fonts"][name],
                        "at": kept.get("at", {}).get(name),
                        "notes": kept["notes"].get(name),
                        "fields": kept["fields"].get(name),
                        "prints": kept["prints"][name],
                        "emphasis": kept["emphasis"][name],
                        "stories": kept.get("stories", {}).get(name),
                        "noteText": kept.get("noteText", {}).get(name),
                        "text": kept.get("text", {}).get(name),
                        "sha256": kept["sha256"][name],
                        "application": kept["application"],
                        "verifier": kept.get("verifier"),
                    },
                )
    differs = False
    unjudged = 0
    for path in paths:
        if not path.read_bytes().startswith(b"PK\x03\x04"):
            # A .doc under a .docx name makes Word convert it, and can leave a dialog open.
            if args.command == "record":
                raise SystemExit(f"{path.name}: not a .docx")
            sys.stdout.write(f"{path.name}: not a .docx; not sent to Word\n")
            unjudged += 1
            continue
        if args.command == "compare" and is_tracked(path):
            # Two texts: each view held to Word's, and Word's held to what Word shows.
            try:
                result = judge_tracked(path)
            except SystemExit as failed:
                sys.stdout.write(f"{failed}\n")
                unjudged += 1
                continue
            differs = differs or result.startswith("differs")
            sys.stdout.write(f"{path.name}: tracked changes, {result}\n")
            sys.stdout.flush()
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            kept = recorded.get(path.name, {})
            if progress and (kept.get("sha256"), kept.get("application"), kept.get("verifier")) == (
                digest,
                application,
                VERIFIER,
            ):
                sys.stdout.write(f"{path.name}: Word's answers on record reused\n")
            else:
                kept = ask(path)
                if progress:
                    kept |= {"sha256": digest, "application": application, "verifier": VERIFIER}
            if progress:
                recorded[path.name] = kept
                progress.write_text(json.dumps(recorded), "utf-8")
        except SystemExit as failed:
            if args.command == "record":
                raise
            # One file Word cannot open or answer for does not stop a comparison of many.
            sys.stdout.write(f"{failed}\n")
            unjudged += 1
            continue
        digests[path.name] = digest
        word = kept["drawn"]
        answers[path.name] = word
        font_answers[path.name] = kept["fonts"]
        if kept.get("at") is not None:
            at_answers[path.name] = kept["at"]
        if kept["notes"] is not None:
            note_answers[path.name] = kept["notes"]
        print_answers[path.name] = kept["prints"]
        emphasis_answers[path.name] = kept["emphasis"]
        if kept["fields"] is not None:
            field_answers[path.name] = kept["fields"]
        if kept.get("stories") is not None:
            story_answers[path.name] = kept["stories"]
        if kept.get("noteText") is not None:
            note_text_answers[path.name] = kept["noteText"]
        if kept.get("text") is not None:
            text_answers[path.name] = kept["text"]
        result = judge(path, kept)
        differs = differs or result.startswith("differs")
        sys.stdout.write(f"{path.name}: {result}\n")
        if result.startswith("reader refuses"):
            sys.stdout.write(f"  Word draws: {json.dumps(word)}\n")
        sys.stdout.flush()
    if args.command == "record":
        record = {
            "application": application,
            "verifier": VERIFIER,
            "sha256": digests,
            "at": at_answers,
            "method": (
                "list labels: convert numbers to text, what each list item gained; fonts and at: "
                "the font Word gave each label and its body paragraph, from its copy saved "
                "after; sha256: the bytes each file's answers are for; text: the "
                "body's text as Word shows it, paragraph by paragraph; note marks: "
                "saved as text, what Word wrote between markers around each mark; fields: the "
                "text between markers around each, as shown and after saving as PDF; prints: the "
                "whole text as shown and after saving as PDF, page numbers aside; emphasis: bold, "
                "italic, caps and strike of each body paragraph's text, between and in field "
                "results; stories: each section's headers and footers by type (unless linked to "
                "the previous section's) with their page-number fields' results, and each "
                "comment's author and text; noteText: each footnote's and endnote's text"
            ),
            "recorded": datetime.date.today().isoformat(),
            "drawn": answers,
            "fonts": font_answers,
            "notes": note_answers,
            "fields": field_answers,
            "prints": print_answers,
            "emphasis": emphasis_answers,
            "stories": story_answers,
            "noteText": note_text_answers,
            "text": text_answers,
        }
        target = args.folder / "word.json"
        target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")
        sys.stdout.write(f"wrote {target}\n")
        if progress:
            progress.unlink(missing_ok=True)
    if unjudged:
        sys.stdout.write(f"{unjudged} file(s) not judged by Word\n")
    return 1 if differs else 2 if unjudged else 0


if __name__ == "__main__":
    raise SystemExit(main())
