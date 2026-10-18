#!/usr/bin/env bash
# Run on commandcenter (the GPU box) to add AI summaries for newly introduced bills and push them to the dashboard.
# The GitHub Action refreshes bill status 3x daily on its own; this is the only part that needs Ollama.
#
#   SESSION=2027 scripts/commandcenter_refresh.sh
#
# Suggested crontab, 40 minutes after each Action run (7:17, 13:17, 19:17 Mountain):  see README.md
set -euo pipefail
YEAR="${SESSION:-2027}"
MONTANA="${MONTANA_DIR:-$HOME/LLM/montana}"      # the pipeline repo
HERE="$(cd "$(dirname "$0")/.." && pwd)"

# one run at a time: a long analysis must not overlap the next scheduled one
exec 9>"${TMPDIR:-$HOME}/montana-dashboard.lock"
flock -n 9 || { echo "previous run still going; skipping"; exit 0; }

cd "$MONTANA"
export SESSION="$YEAR"
python3 fetch_data.py || { rc=$?; [ $rc -eq 2 ] && { echo "no numbered bills yet for $YEAR"; exit 0; }; exit $rc; }
python3 download_texts.py       # skips bills it already has
python3 analyze.py              # skips bills already analysed
python3 synthesize.py           # session narrative (one long GPU call); drop this line to refresh summaries only
cd "$HERE"
python3 scripts/export_analysis.py --year "$YEAR" --montana "$MONTANA" --push
