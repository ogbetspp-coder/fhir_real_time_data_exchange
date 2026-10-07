#!/usr/bin/env bash
# The Word drawing's build (docs/design/certified-word-drawing.md, section 4, "The trigger and the
# build"), one step per call, as the trigger in infra/word-drawing.tf runs them in main's checkout
# (/workspace). Every later step reads what an earlier one wrote under run/; once `exists` finds the
# record, every later step ends at once.
#
#   bash scripts/word-drawing/build.sh exists|pull|parse|fetch|draw 1|draw 2|record|sign
#
# Its inputs, the trigger's options.env: ENVIRONMENT, RECORDS (the record bucket), SUBMISSIONS,
# SIGNING_KEY (word-drawing-hsm's resource name) and IMAGES (the image repository); and, for
# `exists` alone, REQUEST, the base64url of the DrawingRequest's canonical bytes. `exists`, `fetch`
# and `sign` run in the Cloud SDK image, with the network; `pull` and the rest in the Docker
# builder, which runs the drawing image with none.
#
# A refusal or failure exits non-zero with one line on standard error, a closed reason, never a word
# of the label, and nothing is signed.
set -euo pipefail

RUN=run
LOCK=src/render/word-drawing/lock.json
KEYS="src/render/word-drawing/keys/${ENVIRONMENT}"
# The renderer's hardening (scripts/render/run.mjs, HARDENING), with the memory measured for two
# drawings at once on Cloud Build's default machine (the design's "Step 2", e2-standard-2).
HARDENING=(--network none --read-only --tmpfs /tmp --shm-size=1g --cap-drop=ALL
  --security-opt=no-new-privileges --pids-limit=2048 --memory=2g)
# What the drawing reads from the checkout (scripts/render/word-drawing.mjs, MOUNTS, less the
# fixtures and the checks), read-only.
MOUNTS=(zone-a/src label-docx-reader/src qrd/registry fhir/mappings)
# The worker's cap on an upload (src/certified-word/recompute.ts, MAX_UPLOAD_BYTES), and on a
# record's bytes (the design's "How Zone B verifies it", 64 KiB).
MAX_DOCX=$((32 * 1024 * 1024))
MAX_RECORD=$((64 * 1024))
STORAGE=https://storage.googleapis.com
KMS=https://cloudkms.googleapis.com/v1

fail() {
  echo "word-drawing: $*" >&2
  exit 1
}

# The read-only mounts of MOUNTS and of the paths given, into VOLUMES.
VOLUMES=()
mount() {
  local path
  for path in "${MOUNTS[@]}" "$@"; do VOLUMES+=(--volume "$PWD/$path:/work/$path:ro"); done
}

token() { gcloud auth print-access-token; }

# Whether the stored object in $1 is a record this build accepts (scripts/word-drawing/stored.py):
# signed by the pinned key, of this request, environment, key version and image, and, with
# run/record.json there, this build's record but for its commit. Prints the commit it names.
accepted() {
  python3 scripts/word-drawing/stored.py "$1" "${KEYS}/$(<"$RUN/key-version").pem" \
    "$ENVIRONMENT" "$(<"$RUN/key-version")" "$(<"$RUN/image-digest")" "$(<"$RUN/version")" \
    "$RUN/request.json" "${@:2}"
}

# One Cloud Storage or Cloud KMS call: its HTTP status on standard output, its body in $1.
call() {
  local out="$1"
  shift
  curl --silent --show-error --output "$out" --write-out '%{http_code}' \
    --header @<(printf 'Authorization: Bearer %s\n' "$(token)") "$@"
}

# The SHA-256 of the canonical JSON {imageDigest, version}: both values are held to a pattern with
# nothing JSON escapes, so this spelling is RFC 8785's.
drawing_id() {
  printf '{"imageDigest":"%s","version":"%s"}' "$1" "$2" | sha256sum | cut -d' ' -f1
}

# The newest key version pinned for this environment and not revoked: keys/<env>/<n>.pem, and
# keys/<env>/revoked.json, a JSON list of versions.
key_version() {
  python3 - "$KEYS" <<'PY'
import json, pathlib, re, sys
keys = pathlib.Path(sys.argv[1])
listed = keys / "revoked.json"
revoked = json.loads(listed.read_text("utf-8")) if listed.exists() else []
if not isinstance(revoked, list) or not all(type(v) is int for v in revoked):
    sys.exit("revoked.json is not a list of versions")
pinned = [int(p.stem) for p in keys.glob("*.pem") if re.fullmatch(r"[1-9][0-9]*", p.stem)]
usable = sorted(set(pinned) - set(revoked))
if not usable:
    sys.exit(1)
print(usable[-1])
PY
}

exists() {
  mkdir -p "$RUN"
  git rev-parse HEAD >"$RUN/commit"
  # Unpadded base64url, decoded only where it is the one spelling of its bytes.
  python3 - "$RUN/request.json" <<'PY' || fail "refused: request"
import base64, os, pathlib, re, sys
given = os.environ.get("REQUEST", "")
if not re.fullmatch(r"[A-Za-z0-9_-]+", given) or len(given) > 4000:
    sys.exit(1)
raw = base64.urlsafe_b64decode(given + "=" * (-len(given) % 4))
if base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != given:
    sys.exit(1)
pathlib.Path(sys.argv[1]).write_bytes(raw)
PY
  local pinned version digest drawing key object status
  pinned="$(python3 -c '
import json, sys
lock = json.load(open(sys.argv[1], encoding="utf-8"))
print(lock["version"], lock["imageDigests"][sys.argv[2]] or "")' "$LOCK" "$ENVIRONMENT")" ||
    fail "the lock cannot be read"
  version="${pinned% *}"
  digest="${pinned#* }"
  [[ "$version" =~ ^word-drawing/[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
    fail "the lock's version is malformed"
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "no drawing image is pinned for ${ENVIRONMENT}"
  printf '%s/word-drawing@%s' "$IMAGES" "$digest" >"$RUN/image"
  printf '%s' "$digest" >"$RUN/image-digest"
  printf '%s' "$version" >"$RUN/version"
  key_version >"$RUN/key-version" || fail "no key version is pinned for ${ENVIRONMENT}"
  drawing="$(drawing_id "$digest" "$version")"
  key="$(sha256sum <"$RUN/request.json" | cut -d' ' -f1)"
  object="word/${key}/${drawing}/$(<"$RUN/key-version").json"
  printf '%s' "$object" >"$RUN/object"
  status="$(call "$RUN/found" --max-filesize "$MAX_RECORD" \
    "${STORAGE}/storage/v1/b/${RECORDS}/o/${object//\//%2F}?alt=media")" ||
    fail "the object at ${object} is over 64 KiB, or could not be read"
  case "$status" in
    # An object on the path is not taken for a record until it is one: anyone who may write the
    # bucket at project level could have put it there. One that is not ends the build, failed.
    200)
      accepted "$RUN/found" >/dev/null ||
        fail "the object at ${object} is not a record this build accepts"
      touch "$RUN/recorded"
      echo "recorded already: ${object}"
      ;;
    404) echo "to draw: ${object}" ;;
    *) fail "looking for the record answered HTTP ${status}" ;;
  esac
}

pull() { docker pull --quiet "$(<"$RUN/image")" >/dev/null; }

# The request parsed strictly, in the image, before the .docx is read: its canonical JSON exactly,
# and the recompute request's own shape.
parse() {
  mount zone-a/scripts
  docker run --rm --interactive "${HARDENING[@]}" "${VOLUMES[@]}" "$(<"$RUN/image")" \
    /opt/python/python /work/zone-a/scripts/word_drawing_build.py request \
    <"$RUN/request.json" >"$RUN/docx-sha256" || fail "refused: request"
}

# The .docx as the worker reads it: its content address in the submissions bucket, at most 32 MiB,
# the bytes whose SHA-256 the request names.
fetch() {
  local sha status
  sha="$(<"$RUN/docx-sha256")"
  [[ "$sha" =~ ^[0-9a-f]{64}$ ]] || fail "refused: request"
  status="$(call "$RUN/label.docx" --max-filesize "$MAX_DOCX" \
    "${STORAGE}/storage/v1/b/${SUBMISSIONS}/o/uploads%2Fsha256%2F${sha}.docx?alt=media")" ||
    fail "refused: the upload is over 32 MiB, or could not be read"
  [[ "$status" == "200" ]] || fail "reading the upload answered HTTP ${status}"
  (($(wc -c <"$RUN/label.docx") <= MAX_DOCX)) || fail "refused: the upload is over 32 MiB"
  [[ "$(sha256sum <"$RUN/label.docx" | cut -d' ' -f1)" == "$sha" ]] ||
    fail "refused: the upload is not the .docx the request names"
}

# One drawing, in a container of its own, with no network.
draw() {
  mount
  docker run --rm --interactive "${HARDENING[@]}" "${VOLUMES[@]}" \
    --volume "$PWD/$RUN/label.docx:/work/label.docx:ro" "$(<"$RUN/image")" \
    /opt/python/python -m zone_a.drawing /work/label.docx \
    <"$RUN/request.json" >"$RUN/drawn-$1.json" || fail "drawing $1 signs nothing"
}

# The two drawings, byte for byte the same, made the record: the fields the container wrote, and
# the environment, the commit, the image and the key version.
record() {
  mount zone-a/scripts "$RUN"
  docker run --rm "${HARDENING[@]}" "${VOLUMES[@]}" "$(<"$RUN/image")" \
    /opt/python/python /work/zone-a/scripts/word_drawing_build.py record \
    "$ENVIRONMENT" "$(<"$RUN/commit")" "$(<"$RUN/image-digest")" "$(<"$RUN/key-version")" \
    "/work/$RUN/request.json" "/work/$RUN/drawn-1.json" "/work/$RUN/drawn-2.json" \
    >"$RUN/record.json" || fail "the two drawings differ, or are not a record"
}

# Signed with the pinned key version, checked against its pinned public key, and written
# create-if-absent. Another build's record at the same path must be this record, byte for byte.
sign() {
  local commit version digest status stored named
  commit="$(<"$RUN/commit")"
  # Still a first-parent commit of main (R1): fetched again, it is on main's first-parent line.
  git fetch -q --depth=100 origin refs/heads/main
  git rev-list --first-parent FETCH_HEAD | grep -qx "$commit" ||
    fail "${commit} is not a first-parent commit of main"
  version="$(<"$RUN/key-version")"
  digest="$(openssl dgst -sha256 -binary "$RUN/record.json" | base64 -w0)"
  status="$(call "$RUN/signed.json" --request POST --header 'Content-Type: application/json' \
    --data "{\"digest\":{\"sha256\":\"${digest}\"}}" \
    "${KMS}/${SIGNING_KEY}/cryptoKeyVersions/${version}:asymmetricSign")"
  [[ "$status" == "200" ]] || fail "signing answered HTTP ${status}"
  python3 -c 'import base64, json, sys
signed = json.load(open(sys.argv[1]))
sys.stdout.buffer.write(base64.b64decode(signed["signature"], validate=True))' \
    "$RUN/signed.json" >"$RUN/signature" || fail "signing answered no signature"
  # The canonical JSON of {record, signatureBase64}: the record's bytes are canonical already, and
  # the keys are in order. Checked as any stored object is, before it is written: the signature
  # against the pinned key (RSA-PSS, SHA-256, a 32-byte salt, as the worker will verify), since a
  # record that did not verify would hold its path for good.
  printf '{"record":%s,"signatureBase64":"%s"}' "$(<"$RUN/record.json")" \
    "$(base64 -w0 <"$RUN/signature")" >"$RUN/stored.json"
  accepted "$RUN/stored.json" "$RUN/record.json" >/dev/null ||
    fail "the signed record is not one this build accepts, against the pinned key ${version}"
  stored="$(<"$RUN/object")"
  local at="${STORAGE}/upload/storage/v1/b/${RECORDS}/o?uploadType=media&ifGenerationMatch=0"
  status="$(call "$RUN/written.json" --request POST --header 'Content-Type: application/json' \
    --data-binary "@$RUN/stored.json" "${at}&name=${stored//\//%2F}")"
  case "$status" in
    200) echo "recorded: ${stored}" ;;
    # Already there (412), or, should Cloud Storage judge the permission first, refused because
    # replacing needs the delete permission this identity lacks (403): either way the stored
    # record must be this one.
    412 | 403)
      status="$(call "$RUN/existing.json" --max-filesize "$MAX_RECORD" \
        "${STORAGE}/storage/v1/b/${RECORDS}/o/${stored//\//%2F}?alt=media")" ||
        fail "the object at ${stored} is over 64 KiB, or could not be read"
      [[ "$status" == "200" ]] || fail "reading the existing record answered HTTP ${status}"
      # Another build's record of this request: the same record but, where the two builds ran on
      # either side of a merge, for its commit.
      named="$(accepted "$RUN/existing.json" "$RUN/record.json")" ||
        fail "the object at ${stored} is not this build's record (this build ran ${commit}):" \
          "a planted object, or a drawing that is not deterministic"
      echo "recorded already, the same record, by commit ${named}: ${stored}"
      ;;
    *) fail "writing the record answered HTTP ${status}" ;;
  esac
}

step="${1:-}"
[[ "$step" == "exists" || ! -e "$RUN/recorded" ]] || exit 0
case "$step" in
  exists | pull | parse | fetch | record | sign) "$step" ;;
  draw)
    [[ "${2:-}" == "1" || "${2:-}" == "2" ]] || fail "usage: draw 1|2"
    draw "$2"
    ;;
  *) fail "usage: build.sh exists|pull|parse|fetch|draw 1|draw 2|record|sign" ;;
esac
