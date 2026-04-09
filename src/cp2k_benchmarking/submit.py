import argparse
import subprocess
from pathlib import Path


def find_submit_scripts(root: Path) -> list[Path]:
    """
    Recursively find all files named 'submit.sl' under root.
    """
    return sorted(root.rglob("submit.sl"))


def submit_script(path: Path, dry_run: bool = False):
    """
    Submit a single SLURM script using sbatch.
    """
    if dry_run:
        print(f"[DRY-RUN] sbatch {path}")
        return

    print(f"Submitting: {path}")
    subprocess.run(
        ["sbatch", path.name],
        cwd=path.parent,
        check=True,
    )


def run():
    parser = argparse.ArgumentParser(
        description="Recursively find and submit all submit.sl files to SLURM"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be submitted without submitting",
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help="Do not prompt for confirmation",
    )

    parser.add_argument(
        "--root",
        default=".",
        help="Root directory to search from (default: current directory)",
    )

    args = parser.parse_args()

    root = Path(args.root).resolve()
    scripts = find_submit_scripts(root)

    if not scripts:
        print("No submit.sl files found.")
        return

    print(f"Found {len(scripts)} submit.sl files:\n")
    for s in scripts:
        print(f"  {s}")

    if not args.yes and not args.dry_run:
        response = input("\nSubmit all jobs? [y/N] ").strip().lower()
        if response not in {"y", "yes"}:
            print("Aborted.")
            return

    print("")
    for script in scripts:
        submit_script(script, dry_run=args.dry_run)

    print("\nDone.")