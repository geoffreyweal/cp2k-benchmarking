import argparse
import subprocess
import time
from pathlib import Path


# =================================================
# Time parsing utilities
# =================================================

def parse_slurm_time_to_seconds(time_str: str) -> int:
    """
    Convert SLURM time formats to seconds.

    Supported formats:
      MM:SS
      HH:MM:SS
      D-HH:MM:SS
    """
    time_str = time_str.strip()

    days = 0
    if "-" in time_str:
        day_part, time_part = time_str.split("-", 1)
        days = int(day_part)
    else:
        time_part = time_str

    parts = list(map(int, time_part.split(":")))

    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"Invalid SLURM time format: {time_str}")

    return (
        days * 86400 +
        hours * 3600 +
        minutes * 60 +
        seconds
    )


def format_seconds(seconds: int) -> str:
    """
    Format seconds as Dd HH:MM:SS or HH:MM:SS.
    """
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)

    if days > 0:
        return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def extract_walltime_from_submit(script: Path) -> int:
    """
    Extract #SBATCH --time from a submit.sl file.
    """
    for line in script.read_text().splitlines():
        line = line.strip()
        if line.startswith("#SBATCH") and "--time=" in line:
            time_str = line.split("--time=", 1)[1]
            return parse_slurm_time_to_seconds(time_str)

    raise RuntimeError(f"No --time directive found in {script}")


# =================================================
# Submission helpers
# =================================================

def find_submit_scripts(root: Path) -> list:
    """
    Recursively find all submit.sl files under root.
    """
    return sorted(root.rglob("submit.sl"))


def submit_script(path: Path, dry_run: bool = False) -> bool:
    """
    Submit a single submit.sl file using sbatch.
    Returns True on success, False on failure.
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


# =================================================
# Main entry point
# =================================================

def run():
    parser = argparse.ArgumentParser(
        description="Submit all submit.sl files and report walltime totals"
    )

    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of jobs to submit before pausing"
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=1.0,
        help="Pause duration (seconds) after each batch"
    )

    args = parser.parse_args()

    root = Path(args.root).resolve()
    scripts = find_submit_scripts(root)

    if not scripts:
        print("No submit.sl files found.")
        return

    # ---------------------------------------------
    # Initial walltime accounting
    # ---------------------------------------------
    walltimes = {}
    total_walltime_seconds = 0

    for script in scripts:
        wt = extract_walltime_from_submit(script)
        walltimes[script] = wt
        total_walltime_seconds += wt

    remaining_walltime_seconds = total_walltime_seconds
    submitted_walltime_seconds = 0

    print(f"Found {len(scripts)} submit.sl files")
    print(
        f"Total requested walltime before submission: "
        f"{format_seconds(total_walltime_seconds)} "
        f"({total_walltime_seconds:,} seconds)\n"
    )

    if not args.yes and not args.dry_run:
        response = input("Submit all jobs? [y/N] ").strip().lower()
        if response not in {"y", "yes"}:
            print("Aborted.")
            return

    failed = []

    # ---------------------------------------------
    # Submission loop
    # ---------------------------------------------
    for idx, script in enumerate(scripts, start=1):
        ok = submit_script(script, dry_run=args.dry_run)

        if ok:
            submitted_walltime_seconds += walltimes[script]
            remaining_walltime_seconds -= walltimes[script]
        else:
            failed.append(script)

        if idx % args.batch_size == 0 and idx < len(scripts):
            print(
                f"\nSubmitted {idx} jobs — pausing for {args.pause} s...\n"
            )
            time.sleep(args.pause)

    # ---------------------------------------------
    # Final reporting
    # ---------------------------------------------
    print("\nSubmission complete.")

    if failed:
        print("\nThe following jobs failed to submit:")
        for s in failed:
            print(f"  {s}")

    # This is what you asked for:
    print(
        f"\nWalltime of successfully submitted jobs: "
        f"{format_seconds(submitted_walltime_seconds)} "
        f"({submitted_walltime_seconds:,} seconds)"
    )

    if remaining_walltime_seconds > 0:
        print(
            f"Walltime of jobs not submitted: "
            f"{format_seconds(remaining_walltime_seconds)} "
            f"({remaining_walltime_seconds:,} seconds)"
        )
    else:
        print("All jobs were submitted successfully.")