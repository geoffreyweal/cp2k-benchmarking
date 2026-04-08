import sys
from cp2k_benchmarking.qmmm import setup, report


def main():
    if len(sys.argv) < 3:
        print(
            "Usage:\n"
            "  cp2k_benchmarking qmmm setup [options]\n"
            "  cp2k_benchmarking qmmm report"
        )
        sys.exit(1)

    domain = sys.argv[1]    # qmmm (later: md, periodic, etc.)
    command = sys.argv[2]   # setup / report

    # Strip program name + domain + command
    sys.argv = [sys.argv[0]] + sys.argv[3:]

    if domain == "qmmm":
        if command == "setup":
            setup.run()
        elif command == "report":
            report.run()
        else:
            print(f"Unknown qmmm command: {command}")
            sys.exit(1)
    else:
        print(f"Unknown domain: {domain}")
        sys.exit(1)