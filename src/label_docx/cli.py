"""``label-docx FILE``: read a .docx (or an ePI Bundle, JSON) and write its canonical JSON.

Exit status 0 when the document was read, 2 when it was refused (the JSON names the code and the
detail), 1 when the file could not be opened. The output is written to ``--output`` or to
standard output, as bytes, so no platform newline or encoding translation touches it. A refusal's
detail names elements, fonts and codes, never the document's text.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from label_docx.documents import kind
from label_docx.service import browser_verifier, check_environment, serve, word_verifier
from label_docx.store import Store, StoreError


def main(argv: list[str] | None = None) -> int:
    """Run the command line; the return value is the exit status."""
    parser = argparse.ArgumentParser(
        prog="label-docx", description="Read a .docx and write its canonical JSON."
    )
    parser.add_argument("file", type=Path, help="the .docx to read")
    parser.add_argument("-o", "--output", type=Path, help="write here instead of standard output")
    args = parser.parse_args(argv)
    try:
        data = args.file.read_bytes()
    except OSError as error:
        sys.stderr.write(f"label-docx: cannot read {args.file}: {error.strerror}\n")
        return 1
    result, ok = kind(data).read(data)
    if args.output is None:
        sys.stdout.buffer.write(result)
        sys.stdout.buffer.flush()
    else:
        args.output.write_bytes(result)
    return 0 if ok else 2


def service_main(argv: list[str] | None = None) -> int:
    """``label-docx-service``: serve a store over HTTP, or ingest files into it."""
    parser = argparse.ArgumentParser(
        prog="label-docx-service", description="Ingest .docx documents into a write-once store."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serving = commands.add_parser("serve", help="serve the store over HTTP")
    serving.add_argument("--store", type=Path, required=True)
    serving.add_argument("--host", default="127.0.0.1")
    serving.add_argument("--port", type=int, default=8080)
    for command in (serving,):
        command.add_argument(
            "--browser",
            choices=("auto", "on", "off"),
            default="auto",
            help="hold every ePI to Chrome: where installed (auto), always (on), never (off)",
        )
    ingesting = commands.add_parser("ingest", help="ingest files; print one receipt each")
    ingesting.add_argument("--store", type=Path, required=True)
    ingesting.add_argument("--browser", choices=("auto", "on", "off"), default="auto")
    for command in (serving, ingesting):
        command.add_argument(
            "--word",
            choices=("auto", "on", "off"),
            default="off",
            help="hold every .docx to Microsoft Word (macOS; about a minute a document)",
        )
    ingesting.add_argument("files", type=Path, nargs="+")
    verifying = commands.add_parser("verify", help="read every kept document again and compare")
    verifying.add_argument("--store", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(
            args.store,
            args.host,
            args.port,
            browser_verifier(args.browser),
            word_verifier(args.word),
        )
        return 0
    check_environment()
    store = Store(
        args.store,
        browser=browser_verifier(getattr(args, "browser", "off")),
        word=word_verifier(getattr(args, "word", "off")),
    )
    if args.command == "verify":
        failed = 0
        documents = store.documents()
        for document in documents:
            try:
                store.verify(document)
            except StoreError as failure:
                failed += 1
                sys.stderr.write(f"{failure}\n")
        sys.stdout.write(f"{len(documents) - failed} of {len(documents)} documents verified\n")
        return 1 if failed else 0
    refused = False
    for path in args.files:
        ingested = store.ingest(path.read_bytes())
        refused = refused or not ingested.read
        sys.stdout.buffer.write(ingested.receipt)
    sys.stdout.buffer.flush()
    return 2 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
