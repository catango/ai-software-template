#!/usr/bin/env python3
import subprocess
subprocess.run(["gh", "release", "create", "v1"], check=True)
