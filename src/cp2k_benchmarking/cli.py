import sys

from cp2k_benchmarking.qmmm import setup, report
from cp2k_benchmarking import submit


def main():
    if len(sys.argv) < 2:
        print(
            "Usage:\n"
            "  cp2k_benchmarking qmmm setup [options]\n"
            "  cp2k_benchmarking qmmm report\n"
            "  cp2k_benchmarking submit [options]"
        )
        sys.exit(1)

    command = sys.argv[1]

    # Strip program name + command
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    if command == "qmmm":
        if len(sys.argv) < 2:
            print("Usage: cp2k_benchmarking qmmm <setup|report>")
            sys.exit(1)

        subcommand = sys.argv[1]
        sys.argv = [sys.argv[0]] + sys.argv[2:]

        if subcommand == "setup":
            setup.run()
        elif subcommand == "report":
            report.run()
        else:
            print(f"Unknown qmmm command: {subcommand}")
            sys.exit(1)

    elif command == "submit":
        submit.run()

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)