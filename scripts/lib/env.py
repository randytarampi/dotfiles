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
                    # Strip wrapping quotes if any
                    if val.startswith('"') and val.endswith('"'):
                        val = val[1:-1]
                    elif val.startswith("'") and val.endswith("'"):
                        val = val[1:-1]
                    os.environ[key] = val
        alias_github_token()
        return True
    except Exception as e:
        logging.warning(f"Error loading env file {env_file}: {e}")
        return False


def alias_github_token():
    """
    Ensures both GITHUB_TOKEN and GH_TOKEN are set if either is defined.
    """
    github_token = os.environ.get("GITHUB_TOKEN")
    gh_token = os.environ.get("GH_TOKEN")

    if github_token and not gh_token:
        os.environ["GH_TOKEN"] = github_token
    elif gh_token and not github_token:
        os.environ["GITHUB_TOKEN"] = gh_token
