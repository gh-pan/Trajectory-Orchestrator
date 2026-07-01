"""Fake claude that reads one stdin user message, then emits canned stream-json events.

Usage: python fake_claude.py <events_file>
Behaves like `claude --input-format stream-json --output-format stream-json --print`:
reads a JSON user line from stdin, then writes each line of <events_file> to stdout.
"""
import sys
from pathlib import Path


def main():
    events_file = Path(sys.argv[1])
    # consume one user message from stdin (the activation)
    sys.stdin.readline()
    for line in events_file.read_text().splitlines():
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    # mimic claude exiting after the turn
    sys.exit(0)


if __name__ == "__main__":
    main()
