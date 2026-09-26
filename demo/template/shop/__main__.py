"""Usage: python -m shop profile <id> | python -m shop report"""
import sys

from shop.profile import get_profile
from shop.reports import build_report


def main(argv):
    if len(argv) < 1:
        print(__doc__)
        return 1
    command = argv[0]
    if command == "profile":
        profile = get_profile(int(argv[1]) if len(argv) > 1 else 1)
        print(profile)
    elif command == "report":
        print(build_report())
    else:
        print(f"Unknown command: {command}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
