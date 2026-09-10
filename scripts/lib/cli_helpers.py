"""Shared argparse helpers for the dotfiles command-line contract."""

import argparse

import logger


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


class _DeprecatedModelOverrideAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if option_string in ("--local-fallback-role", "--local-fallback-placeholder"):
            replacement = (
                "--role-model"
                if option_string == "--local-fallback-role"
                else "--category-model"
            )
            logger.warning("Deprecated %s; use %s instead", option_string, replacement)
        current = getattr(namespace, self.dest, None) or []
        current.append(values)
        setattr(namespace, self.dest, current)


def add_model_override_args(parser, preset_choices=None):
    parser.add_argument(
        "--local-fallback-preset", metavar="PRESET", choices=preset_choices
    )
    parser.add_argument(
        "--role-model",
        "--local-fallback-role",
        dest="role_models",
        metavar="ROLE=MODEL",
        action=_DeprecatedModelOverrideAction,
        default=None,
    )
    parser.add_argument(
        "--category-model",
        "--local-fallback-placeholder",
        dest="category_models",
        metavar="CATEGORY=MODEL",
        action=_DeprecatedModelOverrideAction,
        default=None,
    )
    return parser


def add_local_fallback_args(parser):
    """Deprecated compatibility alias for add_model_override_args."""
    return add_model_override_args(parser)


def forward_local_fallback_args(args):
    """Deprecated compatibility wrapper using the new override destinations."""
    flags = []
    preset = getattr(args, "local_fallback_preset", None)
    if preset:
        flags.extend(["--local-fallback-preset", preset])
    for role in getattr(args, "role_models", None) or []:
        if role:
            flags.extend(["--role-model", role])
    for placeholder in getattr(args, "category_models", None) or []:
        if placeholder:
            flags.extend(["--category-model", placeholder])
    return flags


def forward_model_override_args(args):
    """Forward model overrides using their canonical option names."""
    return forward_local_fallback_args(args)
