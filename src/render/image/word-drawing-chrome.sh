#!/bin/sh
# The Chrome zone_a.drawing runs in the word-drawing image (Dockerfile.renderer, LABEL_CHROME): the
# pinned chrome-headless-shell, with the arguments it is given. Without its own sandbox, which
# cannot start in the hardened container ("No usable sandbox!", measured in CI's Word drawing job,
# docs/design/certified-word-drawing.md, "Step 1"); the container's isolation stands in for it, as
# for the renderer gate (scripts/render/run.mjs), and its absence is a stated residual.
exec /opt/renderer/chrome-headless-shell-linux64/chrome-headless-shell --no-sandbox "$@"
