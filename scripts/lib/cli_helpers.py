"""Shared argparse helpers for the dotfiles command-line contract."""

import argparse


def add_common_args(parser):
    parser.allow_abbrev = False
    parser.add_argument(
        "--dry-run", action="store_true", help="preview without writing to filesystem"
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="disable backup file creation (backup-on default)",
    )
    return parser


def forward_common_args(args):
    flags = []
    if getattr(args, "dry_run", False):
        flags.append("--dry-run")
    if getattr(args, "no_backup", False):
        flags.append("--no-backup")
    return flags


def add_local_fallback_args(parser):
    parser.add_argument("--local-fallback-preset", metavar="PRESET")
    parser.add_argument("--local-fallback-role", metavar="ROLE", action="append")
    parser.add_argument(
        "--local-fallback-placeholder", metavar="PLACEHOLDER", action="append"
    )
    return parser


def forward_local_fallback_args(args):
    flags = []
    preset = getattr(args, "local_fallback_preset", None)
    if preset:
        flags.extend(["--local-fallback-preset", preset])
    for role in getattr(args, "local_fallback_role", None) or []:
        if role:
            flags.extend(["--local-fallback-role", role])
    for placeholder in getattr(args, "local_fallback_placeholder", None) or []:
        if placeholder:
            flags.extend(["--local-fallback-placeholder", placeholder])
    return flags
