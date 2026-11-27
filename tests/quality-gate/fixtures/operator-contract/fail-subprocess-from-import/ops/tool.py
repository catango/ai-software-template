#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    from subprocess import run as go
    go(["rm", "x"], check=False)


if __name__ == "__main__":
    main()
