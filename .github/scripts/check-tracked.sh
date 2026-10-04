#!/usr/bin/env bash
# The repository hygiene check (CI's `hygiene` job; plan M7 section 9). F1 data isn't
# redistributed, so no tracked file may be data, derived from it, or a secret. It fails on:
#   - anything under data/, models/, mlruns/, mlartifacts/, .fastf1-cache/, web/public/snapshots/
#     or web/public/saved/ (the dataset, results, checkpoints, featured-corner snapshots and saved
#     chat answers, whose charts carry positions derived from car telemetry);
#   - data files (parquet, npz, npy, feather, arrow, pickle, FastF1 cache, databases), model
#     weights (pt, pth, ckpt, onnx, safetensors, joblib) and videos (mp4, mov, m4v, webm, mkv, avi);
#   - a JSON, JSONL, CSV or TSV file that isn't on the list below of config files, synthetic
#     fixtures and the M4 review's records (so a real-data JSON, a snapshot or a saved answer
#     under any other name fails), and a synthetic fixture that names a real event or circuit;
#   - secrets files (.env other than .env.example, *.pem, .modal.toml, .vercel/);
#   - any file over 5 MB but web/package-lock.json;
#   - a name git has to quote (a double quote, backslash or control character), which no rule
#     above could match.
# Sizes come from the index, so it checks what is staged as well as what is committed.
#
#   .github/scripts/check-tracked.sh          (from anywhere in the repository; exit 1 on a problem)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
shopt -s nocasematch

MAX_BYTES=$((5 * 1024 * 1024))

# Data-like text files that may be tracked: everything else with these extensions fails.
allowed_text() {
  case "$1" in
    .claude/launch.json | web/package.json | web/package-lock.json | web/tsconfig.json | web/vercel.json) return 0 ;;
    # Made up: the MCP App dev host's sample results (engine/scripts/export_sample_result.py runs
    # the tools on synthetic data) and the chat's hand-written fixtures (saved-basic.json).
    web/src/mcp-app/sample-*.json | web/src/lib/chat/fixtures/*.json) return 0 ;;
    # The M4 human review's records: findings and verdicts, no telemetry (whether they go public
    # is the user's question D1 in M7's checklist).
    report/m4_review_labels.json | report/m4_review_claude.json) return 0 ;;
    report/m4_review_preread_v1.csv | report/m4_review_queue.csv) return 0 ;;
  esac
  return 1
}

# Synthetic fixtures use made-up places (Sample Grand Prix, Synthetic Circuit, Lakeside, ...).
is_fixture() {
  case "$1" in
    web/src/mcp-app/sample-*.json | web/src/lib/chat/fixtures/*) return 0 ;;
  esac
  return 1
}
# Every event and location name in the processed catalog (2022 on) matches one of these.
REAL_PLACES='bahrain|sakhir|saudi|jeddah|australian|melbourne|albert park|japanese|suzuka|chinese|shanghai|miami|emilia|imola|monaco|monte carlo|canadian|montreal|montréal|spanish|barcelona|catalunya|austrian|spielberg|red bull ring|british|silverstone|french|le castellet|paul ricard|hungarian|hungaroring|budapest|belgian|francorchamps|dutch|zandvoort|italian|monza|azerbaijan|baku|singapore|marina bay|united states|austin|americas|mexican|mexico city|sao paulo|são paulo|interlagos|brazilian|las vegas|qatar|lusail|abu dhabi|yas island|yas marina|madrid'

problems=()
count=0
fixtures=()
problem() { problems+=("  $1: $2"); }

# "<size> <path>" for every file in the index (git links, mode 160000, have no blob to size).
# Non-ASCII names come through as they are; git still quotes a name holding a double quote, a
# backslash or a control character, and such a name fails below (no rule could match it).
listing=$(git -c core.quotePath=false ls-files --stage |
  awk -F '\t' '$1 !~ /^160000/ { split($1, f, " "); print f[2] " " $2 }' |
  git cat-file --batch-check='%(objectsize) %(rest)')

while read -r size path; do
  [ -n "$path" ] || continue
  count=$((count + 1))
  case "$path" in
    \"*) problem "$path" "a name git has to quote (a double quote, backslash or control character): rename it" ;;
  esac
  case "$path" in
    data/* | models/* | mlruns/* | mlartifacts/* | .fastf1-cache/*)
      problem "$path" "under ${path%%/*}/ (local data and models, never committed)" ;;
    web/public/snapshots/*)
      problem "$path" "under web/public/snapshots/ (written from the API at build time; carries positions)" ;;
    web/public/saved/*)
      problem "$path" "under web/public/saved/ (saved chat answers, copied from the API at build time)" ;;
  esac
  case "$path" in
    *.parquet | *.feather | *.arrow | *.npz | *.npy | *.h5 | *.hdf5 | *.pkl | *.pickle | *.ff1pkl | *.duckdb | *.db | *.sqlite | *.sqlite3)
      problem "$path" "a data file" ;;
    *.pt | *.pth | *.ckpt | *.onnx | *.safetensors | *.joblib)
      problem "$path" "model weights (they go to a release, not the repository)" ;;
    *.mp4 | *.mov | *.m4v | *.webm | *.mkv | *.avi)
      problem "$path" "a video (the demo is attached to the README, not committed)" ;;
    *.json | *.jsonl | *.ndjson | *.csv | *.tsv)
      allowed_text "$path" ||
        problem "$path" "not a known config file or synthetic fixture (real data, a snapshot or a saved answer?): list it in this script only if it is made up" ;;
  esac
  case "$path" in
    *.env.example) ;;
    .env | */.env | .env.* | */.env.* | *.pem | .modal.toml | */.modal.toml | .vercel/* | */.vercel/*)
      problem "$path" "a secrets file" ;;
  esac
  if [ "$size" -gt "$MAX_BYTES" ] && [ "$path" != web/package-lock.json ]; then
    problem "$path" "$size bytes, over the 5 MB limit"
  fi
  if is_fixture "$path" && [ -f "$path" ]; then fixtures+=("$path"); fi
done <<<"$listing"

if [ ${#fixtures[@]} -gt 0 ]; then
  while IFS= read -r line; do
    problem "${line%%:*}" "a synthetic fixture naming a real place (\"${line#*:}\"): fixtures stay made up"
  done < <(grep -o -i -w -E "$REAL_PLACES" -- "${fixtures[@]}" /dev/null | sort -u -t: -k1,1)
fi

if [ ${#problems[@]} -gt 0 ]; then
  echo "check-tracked: ${#problems[@]} problem(s) in $count tracked files"
  printf '%s\n' "${problems[@]}"
  exit 1
fi
echo "check-tracked: $count tracked files, ${#fixtures[@]} of them synthetic fixtures: no data, model weights, snapshots, saved answers, videos or secrets, none over 5 MB"
