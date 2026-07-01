"""Fake judge claude: reads one user message, emits a fixed StructuredOutput-style result event.

Usage: python fake_claude_judge.py <pass> <reason>
Emits a result event whose result text contains JSON {"pass": bool, "reason": str}.
"""
import json
import sys


def main():
    do_pass = sys.argv[1] == "true"
    reason = sys.argv[2]
    sys.stdin.readline()
    payload = json.dumps({"pass": do_pass, "reason": reason})
    event = {"type": "result", "subtype": "success", "result": payload}
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
