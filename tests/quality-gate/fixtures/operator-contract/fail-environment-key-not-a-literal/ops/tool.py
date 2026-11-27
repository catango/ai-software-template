#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    name = sys.argv[0]
    print(os.environ[name])


if __name__ == "__main__":
    main()
