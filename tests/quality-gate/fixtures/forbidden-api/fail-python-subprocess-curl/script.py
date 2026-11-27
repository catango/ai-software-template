#!/usr/bin/env python3
import subprocess
subprocess.run(["curl", "-X", "PATCH", "https://api.github.com/repos/o/r"], check=True)
