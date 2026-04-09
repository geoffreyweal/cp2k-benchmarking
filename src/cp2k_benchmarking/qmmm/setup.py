import argparse
import shutil
from pathlib import Path

from tqdm import tqdm


# -------------------------------------------------
# Parsing utilities
# -------------------------------------------------

def parse_cores(core_string: str) -> list:
    """
    Parse a core specification string.

    Examples:
      8
      1-8
      10-16%2
      1-8,10-16%2
    """
    cores = set()

    for block in core_string.split(","):
        block = block.strip()

        if "-" not in block:
            cores.add(int(block))
            continue

        if "%" in block:
            range_part, step_part = block.split("%")
            step = int(step_part)
        else:
            range_part = block
            step = 1

        start, end = map(int, range_part.split("-"))
        for c in range(start, end + 1, step):
            cores.add(c)

    return sorted(cores)


def mpi_openmp_permutations(total_cores: int):
    """
    Generate all (MPI ranks, OpenMP threads) such that:
        MPI * OpenMP = total_cores
    """
    return [
        (ntasks, total_cores // ntasks)
        for ntasks in range(1, total_cores + 1)
        if total_cores % ntasks == 0
    ]


# -------------------------------------------------
# Memory handling (normalised to MB)
# -------------------------------------------------

def parse_mem_value(mem_str: str) -> float:
    """
    Convert SLURM memory strings to MB.

    Examples:
      128G   -> 131072
      2.5G   -> 2560
      2000M  -> 2000
      2000MB -> 2000
    """
    mem_str = mem_str.strip().upper()

    if mem_str.endswith("GB") or mem_str.endswith("G"):
        value = float(mem_str.rstrip("GB").rstrip("G"))
        return value * 1024

    if mem_str.endswith("MB") or mem_str.endswith("M"):
        value = float(mem_str.rstrip("MB").rstrip("M"))
        return value

    raise ValueError(
        f"Unrecognised memory format: {mem_str}. "
        "Use M/MB or G/GB."
    )


# -------------------------------------------------
# Time handling
# -------------------------------------------------

def parse_time_policy(policy: str):
    """
    Parse time policy of the form:

      30:00,15:00,10:00@16,64

    Meaning:
      total_cores <= 16 -> 30:00
      total_cores <= 64 -> 15:00
      otherwise         -> 10:00
    """
    policy = policy.strip()

    if "@" not in policy:
        raise ValueError(
            "Time policy must be of the form TIMES@THRESHOLDS "
            "(e.g. 30:00,15:00,10:00@16,64)"
        )

    times_part, thresholds_part = policy.split("@", 1)
    times = [t.strip() for t in times_part.split(",")]
    thresholds = [int(c.strip()) for c in thresholds_part.split(",")]

    if len(times) != len(thresholds) + 1:
        raise ValueError(
            "Time policy error: number of times must be "
            "exactly one more than the number of thresholds"
        )

    return times, thresholds


def select_time(total_cores: int, times, thresholds) -> str:
    for time_str, max_cores in zip(times, thresholds):
        if total_cores <= max_cores:
            return time_str
    return times[-1]


def parse_slurm_time_to_seconds(time_str: str) -> int:
    """
    Convert SLURM time to seconds.

    Supports:
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
        days * 24 * 3600
        + hours * 3600
        + minutes * 60
        + seconds
    )


def format_seconds(seconds: int) -> str:
    """
    Format seconds as Dd HH:MM:SS or HH:MM:SS
    """
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)

    if days > 0:
        return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"


# -------------------------------------------------
# Main entry point
# -------------------------------------------------

def run():
    parser = argparse.ArgumentParser(
        description=(
            "Set up CP2K QM/MM benchmarking directories "
            "(MPI/OpenMP permutations with memory and time policies)"
        )
    )

    parser.add_argument("--cores", required=True)
    parser.add_argument("--mem", required=True)
    parser.add_argument("--mem-per-cpu", required=True)
    parser.add_argument("--time-policy", required=True)

    args = parser.parse_args()

    core_list = parse_cores(args.cores)

    # ---- Memory (MB) ----
    mem_floor = args.mem
    mem_per_cpu = args.mem_per_cpu
    mem_floor_val = parse_mem_value(mem_floor)
    mem_per_cpu_val = parse_mem_value(mem_per_cpu)

    # ---- Time policy ----
    times, thresholds = parse_time_policy(args.time_policy)

    source_cp2k_files = Path("CP2K_Files")
    benchmark_root = Path("CP2K_Benchmarking")
    job_body_file = Path("cp2k_benchmarking_submit_include.txt")

    if not source_cp2k_files.is_dir():
        raise RuntimeError("CP2K_Files directory not found.")

    if not job_body_file.is_file():
        raise RuntimeError(
            "Missing cp2k_benchmarking_submit_include.txt"
        )

    job_body = job_body_file.read_text().strip()

    # ---- Safety check ----
    for forbidden in ("--mem=", "--mem-per-cpu=", "--mem-per-gpu="):
        if forbidden in job_body:
            print(
                "\nWARNING:\n"
                "Memory directives found in "
                "cp2k_benchmarking_submit_include.txt.\n"
                "These will OVERRIDE setup.py memory policy.\n"
            )
            break

    benchmark_root.mkdir(exist_ok=True)

    # ---- Generate all jobs ----
    jobs = []
    for total_cores in core_list:
        for ntasks, omp in mpi_openmp_permutations(total_cores):
            jobs.append((total_cores, ntasks, omp))

    print(f"\nGenerating {len(jobs)} benchmark configurations\n")

    total_requested_seconds = 0

    with tqdm(total=len(jobs), desc="Creating benchmark configurations") as pbar:
        for total_cores, ntasks, omp in jobs:
            dirname = (
                benchmark_root
                / f"{total_cores}_Cores_{ntasks}_MPI_{omp}_OpenMPI"
            )

            if dirname.exists():
                shutil.rmtree(dirname)
            dirname.mkdir(parents=True)

            for item in source_cp2k_files.iterdir():
                dest = dirname / item.name
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)

            # ---- Memory policy ----
            if total_cores * mem_per_cpu_val > mem_floor_val:
                mem_line = f"#SBATCH --mem-per-cpu={mem_per_cpu}"
            else:
                mem_line = f"#SBATCH --mem={mem_floor}"

            # ---- Time policy ----
            time_value = select_time(total_cores, times, thresholds)
            total_requested_seconds += parse_slurm_time_to_seconds(time_value)

            submit_file = dirname / "submit.sl"
            submit_file.write_text(f"""#!/bin/bash -e

#SBATCH --job-name=cp2k_qmmm_{total_cores}C_{ntasks}MPI_{omp}OMP
#SBATCH --ntasks={ntasks}
#SBATCH --cpus-per-task={omp}
#SBATCH --time={time_value}
{mem_line}
#SBATCH --output=slurm_%j.out
#SBATCH --error=slurm_%j.err

{job_body}
""")

            pbar.update(1)

    print("\nSetup complete.")
    print("Memory and time policies applied correctly.")
    print(
        f"\nTotal requested walltime across all jobs: "
        f"{format_seconds(total_requested_seconds)} "
        f"({total_requested_seconds:,} seconds)"
    )
``