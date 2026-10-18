#!/bin/bash
# Functional tests for scripts/update.sh integrity guards (offline).
#
# Covers the packaged-update security layers end to end with file:// fixtures
# and a fake systemctl (real services are never touched):
#   1  good checksum, no pinned pubkey        -> applies
#   2  checksum mismatch                      -> refuses, install unchanged
#   3  missing manifest (and the skip env)    -> refuses / applies-with-env
#   4  pinned pubkey, unsigned release        -> refuses
#   5  pinned pubkey, valid Ed25519 signature -> applies
#   6  signature over different bytes         -> refuses
#   7  archive member '../escape'             -> refuses before extraction
#   8  plain-HTTP update URL                  -> refuses
#
# Run: bash tests/test_update_sh_integrity.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/../scripts/update.sh"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

PASS=0
FAIL=0
ok()  { PASS=$((PASS+1)); printf 'PASS: %s\n' "$*"; }
bad() { FAIL=$((FAIL+1)); printf 'FAIL: %s\n' "$*"; }

# --- fake systemctl: never touch real services -------------------------
mkdir -p "$T/fakebin"
printf '#!/bin/bash\nexit 0\n' > "$T/fakebin/systemctl"
chmod +x "$T/fakebin/systemctl"

# --- fixture builders ---------------------------------------------------
build_archive() {  # $1=dir $2=version
    rm -rf "$1/stage"
    mkdir -p "$1/stage/ai-controller"
    echo "$2" > "$1/stage/ai-controller/VERSION"
    echo "payload $2" > "$1/stage/ai-controller/README.txt"
    tar -czf "$1/ai-controller-latest.tar.gz" -C "$1/stage" ai-controller
}

make_case() {  # $1=name $2=version
    local c="$T/cases/$1"
    rm -rf "$c"
    mkdir -p "$c"
    echo "$2" > "$c/VERSION"
    build_archive "$c" "$2"
    ( cd "$c" && sha256sum ai-controller-latest.tar.gz > ai-controller-latest.tar.gz.sha256 )
    echo "$c"
}

make_install() {  # $1=dir
    rm -rf "$1"
    mkdir -p "$1"
    echo "1.0.0" > "$1/VERSION"
}

run_update() {  # $1=install $2=url $3=scriptdir
    AI_CONTROLLER_DIR="$1" AI_CONTROLLER_UPDATE_URL="$2" \
    PATH="$T/fakebin:$PATH" bash "$3/update.sh" 2>&1
}

S1="$T/scripts_nokey"
mkdir -p "$S1"
cp "$SRC" "$S1/update.sh"

S2="$T/scripts_key"
mkdir -p "$S2"
cp "$SRC" "$S2/update.sh"

echo "== bash -n"
bash -n "$SRC" && ok "syntax" || bad "syntax"

echo "== 1: good checksum, no pubkey -> applies"
C=$(make_case c1 2.0.0)
I="$T/inst1"
make_install "$I"
out=$(run_update "$I" "file://$C" "$S1")
if [ "$(cat "$I/VERSION")" = "2.0.0" ] && grep -q "Updated to 2.0.0" <<<"$out"; then
    ok "applied"
else
    bad "applied :: $out"
fi

echo "== 2: checksum mismatch -> refuses, install unchanged"
C=$(make_case c2 2.0.0)
I="$T/inst2"
make_install "$I"
echo "deadbeef  ai-controller-latest.tar.gz" > "$C/ai-controller-latest.tar.gz.sha256"
out=$(run_update "$I" "file://$C" "$S1")
if [ "$(cat "$I/VERSION")" = "1.0.0" ] && grep -q "checksum mismatch" <<<"$out"; then
    ok "refused"
else
    bad "refused :: $out"
fi

echo "== 3: missing manifest -> refuses (unless skip env)"
C=$(make_case c3 2.0.0)
rm -f "$C/ai-controller-latest.tar.gz.sha256"
I="$T/inst3"
make_install "$I"
out=$(run_update "$I" "file://$C" "$S1")
if [ "$(cat "$I/VERSION")" = "1.0.0" ] && grep -q "cannot verify update integrity" <<<"$out"; then
    ok "refused without manifest"
else
    bad "refused without manifest :: $out"
fi
out=$(AI_CONTROLLER_SKIP_CHECKSUM=1 run_update "$I" "file://$C" "$S1")
if [ "$(cat "$I/VERSION")" = "2.0.0" ]; then
    ok "skip-checksum applies"
else
    bad "skip-checksum applies :: $out"
fi

echo "== 4: pubkey present, no signature -> refuses"
C=$(make_case c4 2.0.0)
I="$T/inst4"
make_install "$I"
rm -f "$S2/update_pubkey.pem"
openssl genpkey -algorithm ed25519 -out "$T/release_priv.pem" >/dev/null 2>&1
openssl pkey -in "$T/release_priv.pem" -pubout -out "$S2/update_pubkey.pem" >/dev/null 2>&1
out=$(run_update "$I" "file://$C" "$S2")
if [ "$(cat "$I/VERSION")" = "1.0.0" ] && grep -q "no release signature" <<<"$out"; then
    ok "refused unsigned"
else
    bad "refused unsigned :: $out"
fi

echo "== 5: pubkey present, valid signature -> applies"
openssl pkeyutl -sign -inkey "$T/release_priv.pem" -rawin \
    -in "$C/ai-controller-latest.tar.gz" -out "$C/ai-controller-latest.tar.gz.sig"
out=$(run_update "$I" "file://$C" "$S2")
if [ "$(cat "$I/VERSION")" = "2.0.0" ] && grep -q "Signature verified" <<<"$out"; then
    ok "signed applies"
else
    bad "signed applies :: $out"
fi

echo "== 6: signature over different bytes -> refuses"
C=$(make_case c6 2.0.0)
I="$T/inst6"
make_install "$I"
openssl pkeyutl -sign -inkey "$T/release_priv.pem" -rawin \
    -in "$C/VERSION" -out "$C/ai-controller-latest.tar.gz.sig"
out=$(run_update "$I" "file://$C" "$S2")
if [ "$(cat "$I/VERSION")" = "1.0.0" ] && grep -q "signature verification FAILED" <<<"$out"; then
    ok "refused bad sig"
else
    bad "refused bad sig :: $out"
fi

echo "== 7: archive with ../ member -> refuses"
C=$(make_case c7 2.0.0)
I="$T/inst7"
make_install "$I"
python3 - "$C" <<'PY'
import io
import sys
import tarfile
from pathlib import Path

case = Path(sys.argv[1])
with tarfile.open(case / "ai-controller-latest.tar.gz", "w:gz") as tf:
    tf.add(case / "stage" / "ai-controller", arcname="ai-controller")
    data = b"pwned\n"
    info = tarfile.TarInfo("../evil_escape.txt")
    info.size = len(data)
    tf.addfile(info, io.BytesIO(data))
PY
( cd "$C" && sha256sum ai-controller-latest.tar.gz > ai-controller-latest.tar.gz.sha256 )
out=$(run_update "$I" "file://$C" "$S1")
if [ "$(cat "$I/VERSION")" = "1.0.0" ] && grep -q "unsafe paths" <<<"$out"; then
    ok "refused traversal"
else
    bad "refused traversal :: $out"
fi

echo "== 8: plain http URL -> refuses scheme"
C=$(make_case c8 2.0.0)
I="$T/inst8"
make_install "$I"
out=$(run_update "$I" "http://127.0.0.1:9/nope" "$S1")
if grep -q "refusing plain-HTTP" <<<"$out"; then
    ok "refused http"
else
    bad "refused http :: $out"
fi

echo ""
echo "RESULT: $PASS passed, $FAIL failed"
if [ "$FAIL" -ne 0 ]; then
    exit 1
fi
