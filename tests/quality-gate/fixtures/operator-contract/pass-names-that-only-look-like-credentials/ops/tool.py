#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--author")
    parser.add_argument("--path")
    parser.set_defaults(apply=False)
    path = os.environ.get("PATH", "")
    patch = os.environ.get("PATCH_LEVEL", "")
    size = len(path) + len(patch)


if __name__ == "__main__":
    main()
