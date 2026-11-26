import sys

from .parser import parse_file
from .report import render


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != "report":
        print("usage: python -m invoice_tools report FILE", file=sys.stderr)
        return 2
    print(render(parse_file(argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
