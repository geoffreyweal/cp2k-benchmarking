import argparse
import subprocess
import time
from pathlib import Path


# -------------------------------------------------
# Time parsing utilities
# -------------------------------------------------

def parse_slurm_time_to_seconds(time_str: str) -> int:
    """
    Convert SLURM time formats to seconds.
    Supports:
      MM:SS
      HH:MM:SS
      D-HH:MM:SS
    """
    time_str = time_str.strip()

    days = 0
    if "-" in time_str:
        d, t = time_str.split("-", 1)
        days = int(d)
    else:
        t = time_str

    parts = list(map(int, t.split(":")))

    if len(parts) == 2:
        h = 0
        m, s = parts
    elif len(parts) == 3:
        h, m, s = parts
    else:
        raise ValueError(f"Invalid SLURM time format: {time_str}")

    return days * 86400 + h * 3600 + m * 60 + s


def format_seconds(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)

    if days > 0:
        return f"{days}d {h:02d}:{m:02d}:{s:02d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def extract_walltime_from_submit(script: Path) -> int:
    """
    Extract #SBATCH --time from submit.sl and return seconds.
    """
    for line in script.read_text().splitlines():
        line = line.strip()
        if line.startswith("#SBATCH") and "--time=" in line:
            time_str = line.split("--time=", 1)[1]
            return parse_slurm_time_to_seconds(time_str)

    raise RuntimeError(f"No --time directive found in {script}")


# -------------------------------------------------
# Submission logic
# -------------------------------------------------

def find_submit_scripts(root: Path) -> list:
    return sorted(root.rglob("submit.sl"))


def submit_script(path: Path, dry_run: bool = False) -> bool:
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
        description="Submit all submit.sl files and report total walltime"
    )

    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--root", default=".")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--pause", type=float, default=1.0)

    args = parser.parse_args()

    root = Path(args.root).resolve()
    scripts = find_submit_scripts(root)

    if not scripts:
        print("No submit.sl files found.")
        return

    total_walltime_seconds = 0
    walltimes = {}

    for s in scripts:
        wt = extract_walltime_from_submit(s)
        walltimes[s] = wt
        total_walltime_seconds += wt

    print(f"Found {len(scripts)} submit.sl files")
    print(
        f"Total requested walltime: "
        f"{format_seconds(total_walltime_seconds)} "
        f"({total_walltime_seconds:,} seconds)\n"
    )

    if not args.yes and not args.dry_run:
        response = input("Submit all jobs? [y/N] ").strip().lower()
        if response not in {"y", "yes"}:
            print("Aborted.")
            return

    failed = []

    for idx, script in enumerate(scripts, start=1):
        ok = submit_script(script, dry_run=args.dry_run)
        if not ok:
            failed.append(script)

        if idx % args.batch_size == 0 and idx < len(scripts):
            print(
                f"\nSubmitted {idx} jobs — pausing for {args.pause} s...\n"
            )
            time.sleep(args.pause)

    print("\nSubmission complete.")

    if failed:
        print("\nThe following jobs failed to submit:")
        for s in failed:
            print(f"  {s}")

    print(
        f"\nTotal walltime of submitted jobs: "
        f"{format_seconds(total_walltime_seconds)} "
        f"({total_walltime_seconds:,} seconds)"
    )