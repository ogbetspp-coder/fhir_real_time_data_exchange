#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path


FORMATTABLE = {".cjs", ".css", ".js", ".json", ".jsx", ".md", ".mjs", ".ts", ".tsx", ".yaml", ".yml"}


def main() -> None:
    payload = json.load(sys.stdin)
    file_path = Path(payload.get("file_path", ""))
    if (
        not file_path.is_file()
        or file_path.suffix not in FORMATTABLE
        or "node_modules" in file_path.parts
        or not Path("node_modules/.bin/prettier").exists()
    ):
        print("{}")
        return

    environment = {**os.environ, "NO_COLOR": "1"}
    subprocess.run(
        ["node_modules/.bin/prettier", "--write", str(file_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=environment,
        text=True,
    )
    print("{}")


if __name__ == "__main__":
    main()
