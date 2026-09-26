"""Applies a diff produced by ErrLens and re-runs the matching command.

Usage:
    python apply_fix.py bug3 fix.diff
    python apply_fix.py bug3 fix.diff --revert   # undo the diff
"""
import subprocess
import sys

COMMANDS = {
    "bug1": [sys.executable, "-m", "shop", "profile", "1"],
    "bug2": ["node", "scripts/greet.js", "1", "2", "3"],
    "bug3": [sys.executable, "-m", "shop", "report"],
}


def run(cmd):
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd).returncode


def main(argv):
    if len(argv) < 2 or argv[0] not in COMMANDS:
        print(__doc__)
        return 2
    bug, diff_path = argv[0], argv[1]
    revert = "--revert" in argv
    git_cmd = ["git", "apply", "--whitespace=nowarn"]
    if revert:
        git_cmd.append("-R")
    if run(git_cmd + [diff_path]) != 0:
        print("Could not apply the diff.")
        return 1
    print("Diff applied." if not revert else "Diff reverted.")
    code = run(COMMANDS[bug])
    print("\nResult:", "SUCCESS" if code == 0 else f"ERROR (exit code {code})")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
