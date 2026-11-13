#!/usr/bin/env bash
# Identify only the Linux service units emitted by these dotfiles templates.
# Active directives are checked in their proper sections. Never source or eval
# a systemd unit: it may be user-owned or deliberately modified.
dotfiles_managed_systemd_unit_matches() {
  local unit_file="$1" service="$2" home="$3"
  local description prefix suffix

  [[ -f "$unit_file" && ! -L "$unit_file" ]] || return 1
  case "$service" in
  litellm)
    description="LiteLLM proxy gateway"
    prefix="/usr/bin/env -i HOME=%h PATH=/usr/bin:/bin:%h/.local/share/litellm/venv/bin /bin/bash --noprofile --norc -c 'set -a; source \"$home/.local/share/litellm/service.env\"; set +a; exec \"$home/.local/share/litellm/venv/bin/litellm\" --config \"$home/.local/share/litellm/config.yaml\" --host 127.0.0.1 --port "
    suffix=" --num_workers 1'"
    ;;
  openwebui)
    description="Open WebUI"
    prefix="/usr/bin/env -i HOME=%h PATH=/usr/bin:/bin:%h/.local/share/openwebui/venv/bin DATA_DIR=$home/.local/share/openwebui/data GLOBAL_LOG_LEVEL=INFO /bin/bash --noprofile --norc -c 'set -a; source \"$home/.local/share/openwebui/service.env\"; set +a; exec \"$home/.local/share/openwebui/venv/bin/open-webui\" serve --host 127.0.0.1 --port "
    suffix="'"
    ;;
  *)
    return 1
    ;;
  esac

  awk -v expected_description="$description" -v expected_prefix="$prefix" -v expected_suffix="$suffix" '
    function trim(s) {
      sub(/^[ \t]+/, "", s)
      sub(/[ \t]+$/, "", s)
      return s
    }
    /^[ \t]*[;#]/ { next }
    /^[ \t]*\[[^]]+\][ \t]*$/ {
      section = trim($0)
      next
    }
    {
      line = trim($0)
      pos = index(line, "=")
      if (pos < 2) next
      key = trim(substr(line, 1, pos - 1))
      value = trim(substr(line, pos + 1))
      if (key == "Description" && section == "[Unit]") {
        descriptions++
        description = value
      }
      if (key == "ExecStart" && section == "[Service]") {
        exec_starts++
        command = value
      }
    }
    END {
      if (descriptions != 1 || exec_starts != 1 ||
          description != expected_description) exit 1
      if (substr(command, 1, length(expected_prefix)) != expected_prefix) exit 1
      remaining = substr(command, length(expected_prefix) + 1)
      if (length(remaining) <= length(expected_suffix) ||
          substr(remaining, length(remaining) - length(expected_suffix) + 1) != expected_suffix) exit 1
      port = substr(remaining, 1, length(remaining) - length(expected_suffix))
      if (port !~ /^[0-9]+$/) exit 1
    }
  ' "$unit_file"
}
