"""``label-docx FILE``: read a .docx and write its canonical JSON.

Exit status 0 when the document was read, 2 when it was refused (the JSON names the code and the
detail), 1 when the file could not be opened. The output is written to ``--output`` or to
standard output, as bytes, so no platform newline or encoding translation touches it. A refusal's
detail names elements, fonts and codes, never the document's text.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from label_docx.output import read


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
    result, ok = read(data)
    if args.output is None:
        sys.stdout.buffer.write(result)
        sys.stdout.buffer.flush()
    else:
        args.output.write_bytes(result)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
