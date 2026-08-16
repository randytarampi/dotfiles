import os
import logging


def load_env(env_file=None):
    """
    Parses a .env file and loads its key-value pairs into os.environ.
    Defaults to loading from ~/.env if no path is provided.
    """
    if env_file is None:
        env_file = os.path.expanduser("~/.env")

    if not os.path.exists(env_file):
        logging.debug(f"Environment file not found: {env_file}")
        return False

    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith("#"):
                    continue
                # Split at first '='
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    if key.startswith("export "):
                        key = key.split(None, 1)[1].strip()
                    val = val.strip()
                    # Strip inline comments (# outside of quotes)
                    if val.startswith("'"):
                        close = val.find("'", 1)
                        if close != -1:
                            val = val[1:close]
                        else:
                            pass  # No closing quote — leave as-is
                    elif val.startswith('"'):
                        close = val.find('"', 1)
                        if close != -1:
                            val = val[1:close]
                        else:
                            pass  # No closing quote — leave as-is
                    else:
                        # Unquoted — strip at first # preceded by whitespace
                        for i, c in enumerate(val):
                            if c == "#" and (i == 0 or val[i - 1] in " \t"):
                                val = val[:i].strip()
                                break
                    os.environ[key] = val
        alias_github_token()
        return True
    except Exception as e:
        logging.warning(f"Error loading env file {env_file}: {e}")
        return False


def alias_github_token():
    """
    Ensures GH_TOKEN is canonical and derives GITHUB_TOKEN for integrations
    that require GitHub's alternate token name.
    """
    gh_token = os.environ.get("GH_TOKEN")
    if not gh_token:
        gh_token = os.environ.get("GITHUB_TOKEN")

    if gh_token:
        os.environ["GH_TOKEN"] = gh_token
        os.environ["GITHUB_TOKEN"] = gh_token
