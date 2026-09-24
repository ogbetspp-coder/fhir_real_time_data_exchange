# Python style

The Python in `zone-a/` and `agent/` follows the
[Google Python Style Guide](https://google.github.io/styleguide/pyguide.html), enforced by ruff
and mypy in CI (`scripts/check-all.sh`, `.github/workflows/ci.yml`). This note says which of its
rules a tool enforces, which we keep by review, and where we differ on purpose.

## Enforced by a tool

| Guide rule                                                                               | How                                                                                                                                                 |
| ---------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Lint every file (§2.1)                                                                   | ruff with the rule sets in each `pyproject.toml`; mypy `--strict`                                                                                   |
| Type annotations (§2.21, §3.19)                                                          | mypy `--strict`, `warn_unreachable`: stricter than the guide, which encourages annotations but does not require them                                |
| No `assert` for checks that must hold (§2.4.4)                                           | ruff `S101`, outside tests: Python drops an `assert` when run with `-O`, and a fail-closed check must not depend on how the interpreter was started |
| No catch-all `except` (§2.4.4)                                                           | ruff `BLE001` and `E722`; the one deliberate catch-all (the agent's fail-safe turn end, `finish.py`) carries its reason on the line                 |
| No mutable default arguments (§2.12)                                                     | ruff `B006`                                                                                                                                         |
| Implicit false, `is None` (§2.14)                                                        | ruff `E711`, `E712`, `SIM`                                                                                                                          |
| Naming (§3.16)                                                                           | ruff `N`                                                                                                                                            |
| Imports formatted and ordered (§3.13)                                                    | ruff `I`                                                                                                                                            |
| Docstrings: a one-line summary, a blank line, then `Args:`, `Returns:`, `Raises:` (§3.8) | ruff `D` with `convention = "google"`; tests are exempt (a test's name documents it), as are the generated contract models                          |
| Formatting (§3.1–§3.6)                                                                   | `ruff format`                                                                                                                                       |
| Files and sockets closed (§3.11)                                                         | ruff `SIM115`                                                                                                                                       |
| Error-prone constructs pylint catches                                                    | ruff `PLE`, `PLW` (for example an invisible character written literally in a string, a loop variable overwritten in its loop)                       |

## Kept by review

Functions short enough to read (§3.18, about 40 lines), no global mutable state (§2.5), no
power features such as metaclasses or import hacks (§2.19), comprehensions without more than one
`for` (§2.7), and `if __name__ == "__main__":` around a script's work (§3.17).

## Where we differ, and why

- **Line length 100, not 80** (§3.2). The code is dense with regular expressions and Unicode
  tables, and the whole repository, TypeScript included, is formatted at 100. Changing it
  reflows every file and proves nothing.
- **Names imported from modules** (`from zone_a.epi.reader import read_div`), not only modules
  (§2.2). The guide's rule prevents name clashes in a large shared codebase; here every package
  is small, mypy resolves each name, and ruff's `F811` refuses a redefinition.
- **ruff and mypy instead of pylint** (§2.1). ruff implements the pylint checks we enable, and
  mypy `--strict` covers what pylint's type inference guesses at.
