#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.set_defaults(apply=bool(sys.argv))


if __name__ == "__main__":
    main()
