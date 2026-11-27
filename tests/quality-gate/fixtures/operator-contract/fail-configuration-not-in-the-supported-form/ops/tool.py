#!/usr/bin/env python3
"""Reports the state of a working copy. Dry run unless --apply is given."""
import argparse
import os
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="make the change; the default is a dry run")
    args = parser.parse_args()
    home = os.environ.get("HOME", "")
    status = subprocess.run(["git", "status", "--short"], capture_output=True, text=True, check=False)
    print(status.stdout, home, args.apply)


if __name__ == "__main__":
    main()
