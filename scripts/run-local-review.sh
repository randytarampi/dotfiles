#!/usr/bin/env bash
set -euo pipefail

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
# shellcheck source=./scripts/lib/common.sh
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/common.sh"

ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROMPT_FILE="$ROOT/configs/review/code-review-prompt.md"

usage() {
  printf 'Usage: %s [--staged] [--model PROVIDER/MODEL] [--base REF] [--dry-run]\n' "$(basename "$0")"
}

staged=false
dry_run=false
base=HEAD
base_set=false
model=""
while (($# > 0)); do
  case "$1" in
  --help | -h)
    usage
    exit 0
    ;;
  --dry-run)
    dry_run=true
    ;;
  --staged)
    staged=true
    ;;
  --model)
    if (($# < 2)); then
      printf '%s\n' "--model requires a value" >&2
      usage >&2
      exit 2
    fi
    model="$2"
    shift
    ;;
  --base)
    if (($# < 2)); then
      printf '%s\n' "--base requires a value" >&2
      usage >&2
      exit 2
    fi
    base="$2"
    base_set=true
    shift
    ;;
  *)
    printf 'Unknown option: %s\n' "$1" >&2
    usage >&2
    exit 2
    ;;
  esac
  shift
done

if [[ "$staged" == "true" && "$base_set" == "true" ]]; then
  printf '%s\n' "--staged and --base are mutually exclusive" >&2
  usage >&2
  exit 2
fi
if [[ "$base" == -* ]]; then
  die "Base ref must not begin with '-'."
fi

cd "$ROOT"
if [[ -z "$model" ]]; then
  model="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["presets"]["free"]["orchestrator"]["model"])' configs/opencode/oh-my-opencode-slim.json)"
fi

if [[ ! -f "$PROMPT_FILE" ]]; then
  die "Review prompt file is missing: $PROMPT_FILE"
fi
if ! command -v opencode >/dev/null 2>&1 && [[ "$dry_run" != "true" ]]; then
  die "opencode is required for a live review. Install it or use --dry-run."
fi

diff_file="$(mktemp)"
combined_file="$(mktemp)"
truncated_file="$(mktemp)"
trap 'rm -f "$diff_file" "$combined_file" "$truncated_file"' EXIT

if [[ "$staged" == "true" ]]; then
  git diff --cached >"$diff_file"
  diff_stat="$(git diff --cached --stat)"
elif [[ "$base" != "HEAD" ]]; then
  git diff "$base...HEAD" >"$diff_file"
  diff_stat="$(git diff "$base...HEAD" --stat)"
else
  git diff HEAD >"$diff_file"
  diff_stat="$(git diff HEAD --stat)"
fi

untracked_count=0
if [[ "$staged" != "true" ]]; then
  mapfile -t untracked_files < <(git ls-files --others --exclude-standard)
  untracked_count="${#untracked_files[@]}"
  if ((untracked_count > 50)); then
    warn "Found ${untracked_count} untracked files; including only the first 50."
    untracked_files=("${untracked_files[@]:0:50}")
  fi
  for untracked in "${untracked_files[@]}"; do
    git diff --no-index /dev/null "$untracked" >>"$diff_file" || true
  done
fi

if [[ ! -s "$diff_file" ]]; then
  ok "No changes to review"
  exit 0
fi

diff_bytes="$(wc -c <"$diff_file" | tr -d '[:space:]')"
if ((diff_bytes > 100000)); then
  warn "Diff is ${diff_bytes} bytes; truncating the diff portion to 100000 bytes."
  head -c 100000 "$diff_file" >"$truncated_file"
  diff_file="$truncated_file"
fi

if [[ "$staged" == "true" ]]; then
  tracked_count="$(git diff --cached --name-only | wc -l | tr -d '[:space:]')"
else
  tracked_count="$(git diff HEAD --name-only | wc -l | tr -d '[:space:]')"
fi
info "Review model: $model
Diff stat:
${diff_stat:-  (stat unavailable)}
Tracked files: ${tracked_count}; untracked files included: ${untracked_count}"

if [[ "$dry_run" == "true" ]]; then
  info "Dry run: would run opencode with the shared review prompt and diff."
  exit 0
fi

{
  cat "$PROMPT_FILE"
  printf '\n## Diff\n\n'
  cat "$diff_file"
} >"$combined_file"

# The combined prompt is intentionally passed as one argument to opencode.
# shellcheck disable=SC2002
opencode run --model "$model" "$(cat "$combined_file")"
