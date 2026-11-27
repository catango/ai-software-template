"""Code behind the scripts that the Skills call: cr-context (with cr-preflight), cr-criteria, and pr-checkpoint.

The scripts in scripts/ only add the library paths and call `main` of one module here. GitHub is reached through `client.py`
alone (`gh api` with argument arrays, endpoints and GraphQL documents written out as string literals); every other module
works on plain data, so the tests run without GitHub.
"""
