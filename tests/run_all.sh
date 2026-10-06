#!/usr/bin/env bash
# One entry point for production checks: safety tests, the held-out regression gate, and doctor.
# Exit 0 only if all pass. Uses the SDK venv python (pyturso 0.8.1).
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.." || exit 2
PY="$HOME/.local/share/turso/sdk-venv/bin/python"
export HABITAT_CTX_RECEIPTS=/tmp/habitat-ctx-tests-receipts.db HABITAT_CTX_ORIGIN=eval
fail=0
"$PY" tests/test_safety.py | tail -1 || fail=1
"$PY" tests/eval_heldout.py | tail -1 || fail=1
./habitat-ctx doctor | tail -1 || fail=1
rm -f /tmp/habitat-ctx-tests-receipts.db*
[ "$fail" = 0 ] && echo "run_all verdict=PASS" || echo "run_all verdict=FAIL"
exit "$fail"
