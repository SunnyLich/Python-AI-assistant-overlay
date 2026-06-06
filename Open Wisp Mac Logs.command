#!/usr/bin/env bash
# Double-click on macOS to open the newest native Wisp validation log folder.
set -u

cd "$(dirname "$0")"

latest_pointer=""
if [ -d build_logs ]; then
  latest_pointer="$(ls -t build_logs/latest_macos_*.txt 2>/dev/null | head -n 1 || true)"
fi

value_from_pointer() {
  local key="$1" file="$2"
  awk -F= -v wanted="$key" '$1 == wanted { print substr($0, index($0, "=") + 1); exit }' "$file" 2>/dev/null
}

newest_log_dir_from_folders() {
  local newest="" newest_mtime=0 dir mtime
  for dir in build_logs/macos_native_tests_* build_logs/macos_phase1_* build_logs/macos_package_*; do
    [ -d "$dir" ] || continue
    mtime="$(stat -f %m "$dir" 2>/dev/null || echo 0)"
    if [ "$mtime" -gt "$newest_mtime" ]; then
      newest_mtime="$mtime"
      newest="$dir"
    fi
  done
  printf '%s\n' "$newest"
}

if [ -n "$latest_pointer" ]; then
  log_dir="$(value_from_pointer log_dir "$latest_pointer")"
  summary_log="$(value_from_pointer summary_log "$latest_pointer")"
  checklist="$(value_from_pointer live_parity_checklist "$latest_pointer")"
else
  log_dir="$(newest_log_dir_from_folders)"
  summary_log="$log_dir/summary.log"
  checklist="$log_dir/live-parity-checklist.md"
fi

if [ -n "$latest_pointer" ] && { [ -z "${log_dir:-}" ] || [ ! -d "$log_dir" ]; }; then
  echo "Latest pointer is stale; falling back to newest timestamped log folder."
  log_dir="$(newest_log_dir_from_folders)"
  summary_log="$log_dir/summary.log"
  checklist="$log_dir/live-parity-checklist.md"
fi

echo "Latest pointer: ${latest_pointer:-none}"
echo "Log folder: ${log_dir:-none}"
echo "Summary: ${summary_log:-none}"
echo "Checklist: ${checklist:-none}"
echo

if [ -z "${log_dir:-}" ] || [ ! -d "$log_dir" ]; then
  echo "No native macOS log folder found yet."
  echo "Run Test Wisp (Mac Native).command first."
  echo
  read -r -p "Press Return to close..." _ || true
  exit 1
fi

if command -v open >/dev/null 2>&1; then
  open "$log_dir"
  [ -f "$summary_log" ] && open "$summary_log"
  [ -f "$checklist" ] && open "$checklist"
else
  echo "The macOS open command was not found."
fi

echo "Opened latest native macOS logs."
echo
read -r -p "Press Return to close..." _ || true
