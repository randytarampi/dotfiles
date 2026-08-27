"""Shared argparse helpers for the dotfiles command-line contract."""

import argparse


def add_common_args(parser, *, no_backup=False):
    parser.allow_abbrev = False
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be done without writing"
    )
    if no_backup:
        parser.add_argument(
            "--no-backup", action="store_true", help="Skip backup before modifying"
        )
    return parser


def forward_common_args(args):
    flags = []
    if getattr(args, "dry_run", False):
        flags.append("--dry-run")
    if getattr(args, "no_backup", False):
        flags.append("--no-backup")
    return flags


def add_skip_arg(parser, allowed_steps):
    parser.add_argument(
        "--skip",
        default="",
        help=f"Comma-separated steps to skip (allowed: {','.join(allowed_steps)})",
    )
    return parser


def parse_skip(value, allowed_steps):
    if not value:
        return set()
    steps = set(s.strip() for s in value.split(",") if s.strip())
    unknown = steps - set(allowed_steps)
    if unknown:
        import sys

        print(
            f"Error: unknown skip step(s): {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(allowed_steps))}",
            file=sys.stderr,
        )
        sys.exit(2)
    return steps


def add_min_reasoning_embedding_arg(parser):
    parser.add_argument(
        "--min-reasoning-embedding",
        type=int,
        default=None,
        help="Minimum embedding length for reasoning models (0=disabled)",
    )
    return parser


def forward_min_reasoning_embedding_arg(args):
    result = []
    if args.min_reasoning_embedding is not None:
        result.extend(["--min-reasoning-embedding", str(args.min_reasoning_embedding)])
    return result


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
