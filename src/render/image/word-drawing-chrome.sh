#!/bin/sh
# The Chrome zone_a.drawing runs in the word-drawing image (Dockerfile.renderer, LABEL_CHROME): the
# pinned chrome-headless-shell, with the arguments it is given.
exec /opt/renderer/chrome-headless-shell-linux64/chrome-headless-shell "$@"
