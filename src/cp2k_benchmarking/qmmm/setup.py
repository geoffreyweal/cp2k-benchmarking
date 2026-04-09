import argparse
import shutil
from pathlib import Path

from tqdm import tqdm


def parse_cores(core_string: str) -> list:
    """
    Parse a core specification string.

    Examples:
      8                    -> [8]
      1-8                  -> [1,2,3,4,5,6,7,8]
      10-16%2              -> [10,12,14,16]
      1-8,10-16%2          -> [1,2,3,4,5,6,7,8,10,12,14,16]
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
    Generate all (MPI ranks, OpenMP threads) pairs
    such that MPI * OpenMP = total cores.
    """
    return [
        (ntasks, total_cores // ntasks)
        for ntasks in range(1, total_cores + 1)
        if total_cores % ntasks == 0
    ]


def parse_mem_value(mem_str: str) -> float:
    """
    Extract numeric value from SLURM memory strings like 128G, 2000M, 2.5G.
    Units are assumed consistent between --mem and --mem-per-cpu.
    """
    return float("".join(c for c in mem_str if c.isdigit() or c == "."))


def parse_time_policy(policy: str):
    """
    Parse time policy of the form:

      30:00,15:00,10:00@16,64

    Meaning:
      total_cores <= 16  -> 30:00
      total_cores <= 64  -> 15:00
      total_cores >  64  -> 10:00
    """
    policy = policy.strip()

    if "@" not in policy:
        raise ValueError(
            "Time policy must be of the form TIMES@THRESHOLDS, "
            "e.g. 30:00,15:00,10:00@16,64"
        )

    times_part, thresholds_part = policy.split("@", 1)

    times = [t.strip() for t in times_part.split(",")]
    thresholds = [int(c.strip()) for c in thresholds_part.split(",")]

    if len(times) != len(thresholds) + 1:
        raise ValueError(
            "Time policy error: number of times must be exactly "
            "one more than the number of core thresholds"
        )

    return times, thresholds


def select_time(total_cores: int, times, thresholds) -> str:
    """
    Select the appropriate walltime for a given total core count.
    """
    for time_str, max_cores in zip(times, thresholds):
        if total_cores <= max_cores:
            return time_str
    return times[-1]


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

    mem_floor = args.mem
    mem_per_cpu = args.mem_per_cpu
    mem_floor_val = parse_mem_value(mem_floor)
    mem_per_cpu_val = parse_mem_value(mem_per_cpu)

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

    # -------------------------------------------------
    # SAFETY CHECK: memory directives must NOT be here
    # -------------------------------------------------
    for forbidden in ("--mem=", "--mem-per-cpu=", "--mem-per-gpu="):
        if forbidden in job_body:
            print(
                "\nWARNING:\n"
                "Memory directives were found in "
                "cp2k_benchmarking_submit_include.txt.\n"
                "These will OVERRIDE the memory policy defined in setup.py.\n"
                "Please remove all --mem*, --mem-per-cpu*, and --mem-per-gpu*\n"
                "directives from the include file.\n"
            )
            break

    benchmark_root.mkdir(exist_ok=True)

    # -------------------------------------------------
    # Precompute all benchmark jobs
    # -------------------------------------------------
    jobs = []
    for total_cores in core_list:
        for ntasks, omp in mpi_openmp_permutations(total_cores):
            jobs.append((total_cores, ntasks, omp))

    print(f"\nGenerating {len(jobs)} benchmark configurations\n")

    # -------------------------------------------------
    # Create directories + submit.sl files
    # -------------------------------------------------
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

            # -------------------------
            # Memory policy
            # -------------------------
            if total_cores * mem_per_cpu_val > mem_floor_val:
                mem_line = f"#SBATCH --mem-per-cpu={mem_per_cpu}"
            else:
                mem_line = f"#SBATCH --mem={mem_floor}"

            # -------------------------
            # Time policy
            # -------------------------
            time_value = select_time(total_cores, times, thresholds)

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