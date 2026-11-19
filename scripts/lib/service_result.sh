#!/usr/bin/env bash

# Shared result vocabulary for Makefile service targets.
#
# A recipe line completing is not evidence that a service is running. These
# markers keep four distinct outcomes apart, because the previous `|| true`
# plus unconditional "restarted" echo reported success for every one of them:
#
#   SKIPPED  the gate is off or the platform does not support the service.
#            Not a claim that anything is running.
#   APPLIED  the service manager accepted the action.
#   HEALTHY  a protocol or consumer probe passed after the action.
#   FAILED   the requested action did not take effect. Non-zero exit.

svc_skip() { printf 'SKIPPED: %s\n' "$*"; }
svc_applied() { printf 'APPLIED: %s\n' "$*"; }
svc_healthy() { printf 'HEALTHY: %s\n' "$*"; }
svc_failed() {
  printf 'FAILED: %s\n' "$*" >&2
  return 1
}

# True when a user-level systemd instance is reachable. A headless host
# without one is a documented skip, not a service failure.
svc_user_systemd_available() {
  systemctl --user list-unit-files >/dev/null 2>&1
}
