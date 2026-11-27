"""Run a command as an argument array (never through a shell) and return (exit code, output)."""
import subprocess

MAX_OUTPUT = 8192


def run_command(argv, env=None, timeout=None):
    try:
        done = subprocess.run(list(argv), env=env, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)
    except FileNotFoundError:
        return 127, "command not found"
    except PermissionError:
        return 126, "command not executable"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return done.returncode, ((done.stdout or "") + (done.stderr or ""))[:MAX_OUTPUT]
