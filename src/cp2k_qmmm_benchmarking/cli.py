import sys
from cp2k_qmmm_benchmarking import setup, report


def main():
    if len(sys.argv) < 2:
        print("Usage: cp2k-qmmm-benchmark <setup|report>")
        sys.exit(1)

    command = sys.argv[1]

    if command == "setup":
        setup.run()
    elif command == "report":
        report.run()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)