#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    value = os.environ.get("AUTH_HEADER")
    size = len(value or "")


if __name__ == "__main__":
    main()
