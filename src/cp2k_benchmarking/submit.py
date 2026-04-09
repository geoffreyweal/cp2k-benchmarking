import argparse
import subprocess
import time
from pathlib import Path


def find_submit_scripts(root: Path) -> list:
    """
    Recursively find all files named 'submit.sl' under root.
    """
    return sorted(root.rglob("submit.sl"))


def submit_script(path: Path, dry_run: bool = False) -> bool:
    """
    Submit a single SLURM script using sbatch.
    Returns True if submission succeeded, False otherwise.
    """
    if dry_run:
        print(f"[DRY-RUN] sbatch {path}")
        return True

    print(f"Submitting: {path}")

    result = subprocess.run(
        ["sbatch", path.name],
        cwd=path.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        print(f"[FAILED] {path}")
        print(result.stderr.strip())
        return False

    print(result.stdout.strip())
    return True


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

    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of jobs to submit before pausing (default: 10)",
    )

    parser.add_argument(
        "--pause",
        type=float,
        default=1.0,
        help="Pause duration in seconds after each batch (default: 1.0)",
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
    failed = []

    for idx, script in enumerate(scripts, start=1):
        ok = submit_script(script, dry_run=args.dry_run)
        if not ok:
            failed.append(script)

        # Pause after every batch_size submissions (except at the very end)
        if (
            idx % args.batch_size == 0
            and idx < len(scripts)
        ):
            print(
                f"\nSubmitted {idx} jobs — pausing for {args.pause} s...\n"
            )
            time.sleep(args.pause)

    print("\nSubmission complete.")

    if failed:
        print("\nThe following submissions failed:")
        for s in failed:
            print(f"  {s}")
``