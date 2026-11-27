#!/usr/bin/env python3
import subprocess
subprocess.run(["git", "push", "origin", "--tags"], check=True)
