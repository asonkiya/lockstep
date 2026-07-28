#!/bin/bash
# Regression runner for every host-side gate in the dream/ tree.
#
# Runs each gate, checks for its expected PASS line, and prints a summary table
# plus a final ALL-GATES verdict. One failing gate does NOT abort the run — all
# gates run, results are collected, and the runner exits non-zero iff any gate
# FAILed. Gates that require docker are SKIPped (not FAILed) when docker is
# unavailable, so the runner is useful on any host.
#
# Env (inherited if already set):
#   KSRC  kernel source tree (used by the cluster gate)
#   PATH  must include ~/.cargo/bin for rustc (used by several gates)
set -uo pipefail

export KSRC="${KSRC:-/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux}"
export PATH="$HOME/.cargo/bin:$PATH"

DREAM="$(cd "$(dirname "$0")/.." && pwd)"

# docker availability — SKIP docker-dependent gates cleanly if absent
if docker info >/dev/null 2>&1; then DOCKER_OK=1; else DOCKER_OK=0; fi

# result accumulators (parallel arrays: name / status / detail)
NAMES=(); STATUSES=(); DETAILS=()
n_pass=0; n_fail=0; n_skip=0

record() { # name status detail
  NAMES+=("$1"); STATUSES+=("$2"); DETAILS+=("$3")
  case "$2" in
    PASS) n_pass=$((n_pass+1));;
    FAIL) n_fail=$((n_fail+1));;
    SKIP) n_skip=$((n_skip+1));;
  esac
}

# run_gate <label> <expected-PASS-substring> <needs-docker:0|1> <cmd...>
run_gate() {
  local label="$1" expect="$2" needs_docker="$3"; shift 3
  echo "############################################################"
  echo "# GATE: $label"
  echo "############################################################"
  if [ "$needs_docker" = 1 ] && [ "$DOCKER_OK" != 1 ]; then
    echo "  [skipped: docker unavailable]"
    record "$label" SKIP "docker unavailable"
    echo
    return
  fi
  local out rc
  out="$("$@" 2>&1)"; rc=$?
  echo "$out"
  if [ $rc -eq 0 ] && printf '%s' "$out" | grep -qF "$expect"; then
    record "$label" PASS "$expect"
  else
    # distinguish a nonzero exit from a missing PASS line for the detail column
    if [ $rc -ne 0 ]; then
      record "$label" FAIL "exit=$rc, expected: $expect"
    else
      record "$label" FAIL "PASS line not found: $expect"
    fi
  fi
  echo
}

# ---- the gates --------------------------------------------------------------
run_gate "hostdiff"       "HOSTDIFF GATE: PASS"          0 bash "$DREAM/hostdiff/gate.sh"
run_gate "cluster"        "CLUSTER WEAVING GATE: PASS"   0 bash "$DREAM/cluster/gate.sh"
run_gate "mirror"         "MIRROR LIBRARY GATE: PASS"    1 bash "$DREAM/mirror/gate.sh"
run_gate "mmiogen"        "MMIOGEN GATE: PASS"           0 bash "$DREAM/mmiogen/gate.sh"
run_gate "recorder:correct" "RECORDER GATE (correct): PASS" 0 bash "$DREAM/recorder/gate.sh" correct
run_gate "recorder:subtle"  "RECORDER GATE (subtle): PASS"  0 bash "$DREAM/recorder/gate.sh" subtle

# ---- summary table ----------------------------------------------------------
echo "============================================================"
echo "  GATE REGRESSION SUMMARY"
echo "============================================================"
printf "  %-20s %-6s %s\n" "GATE" "RESULT" "DETAIL"
printf "  %-20s %-6s %s\n" "----" "------" "------"
for i in "${!NAMES[@]}"; do
  printf "  %-20s %-6s %s\n" "${NAMES[$i]}" "${STATUSES[$i]}" "${DETAILS[$i]}"
done
echo "------------------------------------------------------------"
printf "  %d passed, %d failed, %d skipped (of %d)\n" \
  "$n_pass" "$n_fail" "$n_skip" "${#NAMES[@]}"
echo

if [ "$n_fail" -eq 0 ]; then
  echo "ALL-GATES: PASS ($n_pass passed, $n_skip skipped)"
  exit 0
else
  echo "ALL-GATES: FAIL ($n_fail failed, $n_pass passed, $n_skip skipped)"
  exit 1
fi
