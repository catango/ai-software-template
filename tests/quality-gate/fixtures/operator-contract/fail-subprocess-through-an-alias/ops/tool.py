#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    import subprocess as sp
    sp.run(["rm", "-rf", "x"], check=False)


if __name__ == "__main__":
    main()
