import argparse
import os
import shutil
from pathlib import Path

from tqdm import tqdm


def parse_cores(core_string: str) -> list:
    """
    Parse a core specification string.

    Examples
    --------
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
    Generate all (ntasks, cpus-per-task) pairs
    such that ntasks * cpus-per-task = total_cores.
    """
    return [
        (ntasks, total_cores // ntasks)
        for ntasks in range(1, total_cores + 1)
        if total_cores % ntasks == 0
    ]


def parse_mem_value(mem_str: str) -> float:
    """
    Extract numeric value from SLURM memory strings like:
    16G, 2000M, 2.5G

    Units are assumed consistent between --mem and --mem-per-cpu.
    """
    return float("".join(c for c in mem_str if c.isdigit() or c == "."))


def parse_time_policy(policy: str):
    """
    Parse time policy of the form:

      30:00,15:00,10:00->16,64

    Meaning:
      total_cores <= 16  -> 30:00
      total_cores <= 64  -> 15:00
      total_cores >  64  -> 10:00
    """
    import pdb; pdb.set_trace()
    if "->" not in policy:
        raise ValueError(
            "Time policy must be of the form TIMES->THRESHOLDS, "
            "e.g. 30:00,15:00,10:00->16,64"
        )

    times_part, thresholds_part = policy.split("->")

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
    Select walltime for a given total core count
    using the parsed time policy.
    """
    for time_str, max_cores in zip(times, thresholds):
        if total_cores <= max_cores:
            return time_str

    # Fallback (last time value)
    return times[-1]


def run():
    parser = argparse.ArgumentParser(
        description="Set up CP2K QM/MM benchmarking directories "
                    "(MPI/OpenMP permutations with memory and time policies)"
    )

    parser.add_argument(
        "--cores",
        required=True,
        help="Core specification, e.g. 8, 1-32%2, or 8,16,32",
    )

    parser.add_argument(
        "--mem",
        required=True,
        help="Minimum total memory per job, e.g. 16G",
    )

    parser.add_argument(
        "--mem-per-cpu",
        required=True,
        help="Memory per CPU, e.g. 2G",
    )

    parser.add_argument(
        "--time-policy",
        required=True,
        help="Time policy, e.g. 30:00,15:00,10:00->16,64",
    )

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

    # -------------------------
    # Sanity checks
    # -------------------------

    if not source_cp2k_files.is_dir():
        raise RuntimeError("CP2K_Files directory not found.")

    if not job_body_file.is_file():
        raise RuntimeError(
            "Missing cp2k_benchmarking_submit_include.txt\n"
            "This file must contain the full job body."
        )

    job_body = job_body_file.read_text().strip()
    benchmark_root.mkdir(exist_ok=True)

    # -------------------------
    # Precompute all jobs
    # -------------------------

    jobs = []
    for total_cores in core_list:
        for ntasks, omp in mpi_openmp_permutations(total_cores):
            jobs.append((total_cores, ntasks, omp))

    print(f"\nGenerating {len(jobs)} benchmark configurations\n")

    # -------------------------
    # Generate directories
    # -------------------------

    with tqdm(total=len(jobs), desc="Creating benchmark configurations") as pbar:
        for total_cores, ntasks, omp in jobs:
            dirname = (
                benchmark_root
                / f"{total_cores}_Cores_{ntasks}_MPI_{omp}_OpenMPI"
            )

            if dirname.exists():
                shutil.rmtree(dirname)

            dirname.mkdir(parents=True)

            # Copy CP2K input files
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

            # -------------------------
            # Write submit.sl
            # -------------------------

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

            os.chmod(submit_file, 0o755)
            pbar.update(1)

    print("\nSetup complete.")
    print("Memory and time policies applied successfully.")