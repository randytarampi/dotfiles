import fnmatch


def filter_mcp_templates(template_list, include_patterns, exclude_patterns):
    """
    Filters a list of templates by include and exclude glob patterns.
    - template_list: list of strings (e.g. ['betterstack', 'github', 'sentry'])
    - include_patterns: list of glob patterns (or comma-separated string)
    - exclude_patterns: list of glob patterns (or comma-separated string)
    Returns: filtered list of template names.
    """
    if isinstance(template_list, str):
        template_list = template_list.split()

    if isinstance(include_patterns, str):
        include_patterns = [p.strip() for p in include_patterns.split(",") if p.strip()]
    elif not include_patterns:
        include_patterns = []

    if isinstance(exclude_patterns, str):
        exclude_patterns = [p.strip() for p in exclude_patterns.split(",") if p.strip()]
    elif not exclude_patterns:
        exclude_patterns = []

    result = []
    for tpl in template_list:
        included = True

        # If include patterns are specified, must match at least one
        if include_patterns:
            included = False
            for pat in include_patterns:
                if fnmatch.fnmatch(tpl, pat):
                    included = True
                    break

        excluded = False
        if exclude_patterns:
            for pat in exclude_patterns:
                if fnmatch.fnmatch(tpl, pat):
                    excluded = True
                    break

        if included and not excluded:
            result.append(tpl)

    return result
