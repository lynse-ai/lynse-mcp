#!/bin/bash
# Package the current tree and deploy it to the www.lynse.ai test instance
# through Tencent Cloud TAT, mirroring docs/deployment-www-lynse-ai-test.md.
#
# Prerequisites:
#   - Tencent CLI (tccli) installed with default credentials (~/.tccli).
#     A throwaway prefix install works: pip install --prefix /tmp/lynse-mcp-tccli tccli
#   - scripts/tat_cli.py (default target instance/region are baked in there).
#
# Usage: scripts/deploy_ai_test.sh [--cli /path/to/tccli]
set -euo pipefail
cd "$(dirname "$0")/.."

CLI=${LYNSE_TCCLI_BIN:-/tmp/lynse-mcp-tccli/bin/tccli}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --cli) CLI=$2; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
[ -x "$CLI" ] || { echo "tccli not found at $CLI" >&2; exit 2; }
SITE_PACKAGES=$(find "$(dirname "$CLI")/.." -type d -name site-packages 2>/dev/null | head -1)
export PYTHONPATH="${SITE_PACKAGES:-$PYTHONPATH}"

TAT() { python3 scripts/tat_cli.py --cli "$CLI" "$@"; }

# 1. Stage an explicit release tree. Local packaging tooling tests depend on
#    scripts/ and repo-relative paths; they stay out of the image test gate.
STAGE=$(mktemp -d /tmp/lynse-ai-release.XXXXXX)
trap 'rm -rf "$STAGE"' EXIT
cp lynse_mcp.py lynse_auth.py lynse_remote.py lynse_account.py lynse_oauth_store.py \
   lynse_ui.py lynse_test_client.py requirements.txt requirements-mcp.txt \
   requirements-remote.txt requirements-dev.txt Dockerfile "$STAGE/"
cp -R deploy "$STAGE/deploy"
cp -R tests "$STAGE/tests"
rm -f "$STAGE"/tests/test_aliyun_cli.py "$STAGE"/tests/test_vendor_cli.py \
      "$STAGE"/tests/test_workbuddy_package.py
mkdir -p "$STAGE/web/dist"
cp web/dist/lynse-app.html "$STAGE/web/dist/"
find "$STAGE" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

RELEASE_TS=$(date +%Y%m%d-%H%M%S)
TARBALL=/tmp/lynse-ai-release-$RELEASE_TS.tar.gz
COPYFILE_DISABLE=1 tar czf "$TARBALL" -C "$STAGE" .
LOCAL_SHA=$(shasum -a 256 "$TARBALL" | cut -d' ' -f1)
echo "release: ai-test-$RELEASE_TS  sha256: $LOCAL_SHA  size: $(wc -c < "$TARBALL" | tr -d ' ')"

# 2. Reset any stale upload buffer on the instance, then push 33,000-byte
#    chunks. The size is a multiple of 3 so no chunk carries base64 padding;
#    mid-stream padding would stop GNU `base64 -d` after the first chunk.
printf 'rm -f /tmp/lynse-ai-release.b64 && echo cleaned\n' > "$STAGE/clean.sh"
CLEAN_INV=$(TAT run "$STAGE/clean.sh" --timeout 60 | python3 -c 'import json,sys; print(json.load(sys.stdin)["InvocationId"])')
sleep 5
TAT poll "$CLEAN_INV" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read().splitlines()[-1]); assert d["status"] == "SUCCESS", d'

split -b 33000 "$TARBALL" "$STAGE/chunk-"
for chunk in "$STAGE"/chunk-*; do
    b64=$(base64 -i "$chunk" | tr -d '\n')
    printf "printf %%s '%s' >> /tmp/lynse-ai-release.b64\n" "$b64" > "$STAGE/upload.sh"
    inv=$(TAT run "$STAGE/upload.sh" --timeout 60 | python3 -c 'import json,sys; print(json.load(sys.stdin)["InvocationId"])')
    state=Missing
    for _ in $(seq 1 15); do
        sleep 3
        state=$(TAT poll "$inv" 2>/dev/null | python3 -c 'import json,sys
lines = [l for l in sys.stdin.read().splitlines() if l.strip()]
try:
    last = json.loads(lines[-1]) if lines else {}
except Exception:
    last = {}
print(last.get("status", "Unknown"))' || echo ParseFail)
        case "$state" in SUCCESS) break ;; FAILED|Timeout|Cancelled) echo "chunk upload failed: $chunk ($state)" >&2; exit 1 ;; esac
    done
    [ "$state" = "SUCCESS" ] || { echo "chunk stuck: $chunk ($state)" >&2; exit 1; }
done

# 3. Decode, verify the checksum, extract into a fresh release directory and
#    run the install script (image build, in-image tests, rolling restart).
cat > "$STAGE/install.sh" <<INSTALL
set -eu
RELEASE=/opt/lynse-mcp-ai-test/releases/ai-test-$RELEASE_TS
mkdir -p "\$RELEASE"
base64 -d /tmp/lynse-ai-release.b64 > /tmp/lynse-ai-release.tar.gz
echo '$LOCAL_SHA  /tmp/lynse-ai-release.tar.gz' | sha256sum -c -
tar xzf /tmp/lynse-ai-release.tar.gz -C "\$RELEASE"
rm -f /tmp/lynse-ai-release.b64 /tmp/lynse-ai-release.tar.gz
sh "\$RELEASE/deploy/install-lynse-ai-test.sh"
INSTALL
inv=$(TAT run "$STAGE/install.sh" --timeout 900 | python3 -c 'import json,sys; print(json.load(sys.stdin)["InvocationId"])')
echo "install invocation: $inv"
state=Missing
for _ in $(seq 1 60); do
    sleep 10
    state=$(TAT poll "$inv" 2>/dev/null | python3 -c 'import json,sys
lines = [l for l in sys.stdin.read().splitlines() if l.strip()]
try:
    last = json.loads(lines[-1]) if lines else {}
except Exception:
    last = {}
print(last.get("status", "Unknown"))' || echo ParseFail)
    case "$state" in SUCCESS|FAILED|Timeout|Cancelled) break ;; esac
    echo "install: $state"
done
TAT poll "$inv" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read().splitlines()[-1])
print("exit:", d["exit_code"])
print(d["output"][-1500:])'
[ "$state" = "SUCCESS" ] || { echo "install failed ($state)" >&2; exit 1; }
echo "deployed ai-test-$RELEASE_TS"
