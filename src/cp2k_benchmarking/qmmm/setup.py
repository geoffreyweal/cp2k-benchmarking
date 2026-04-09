import argparse
import os
import shutil
from pathlib import Path

from tqdm import tqdm


def parse_cores(core_string: str) -> list:
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
    return [
        (ntasks, total_cores // ntasks)
        for ntasks in range(1, total_cores + 1)
        if total_cores % ntasks == 0
    ]


def parse_mem_value(mem_str: str) -> float:
    return float("".join(c for c in mem_str if c.isdigit() or c == "."))


def parse_time_policy(policy: str):
    """
    Parse time policy of the form:

      30:00,15:00,10:00->16,64,128

    Returns:
      [(30:00, 16), (15:00, 64), (10:00, 128)]
    """
    times_part, cores_part = policy.split("->")

    times = [t.strip() for t in times_part.split(",")]
    cores = [int(c.strip()) for c in cores_part.split(",")]

    if len(times) != len(cores):
        raise ValueError(
            "Time policy error: number of times must match number of core thresholds"
        )

    return list(zip(times, cores))


def select_time(total_cores: int, time_policy):
    for time_str, max_cores in time_policy:
        if total_cores <= max_cores:
            return time_str
    raise RuntimeError(
        f"No time defined for total cores = {total_cores}"
    )


def run():
    parser = argparse.ArgumentParser(
        description="Set up CP2K QM/MM benchmarking directories "
                    "(MPI/OpenMP permutations with memory and time policies)"
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

    time_policy = parse_time_policy(args.time_policy)

    source_cp2k_files = Path("CP2K_Files")
    benchmark_root = Path("CP2K_Benchmarking")
    job_body_file = Path("cp2k_benchmarking_submit_include.txt")

    if not source_cp2k_files.is_dir():
        raise RuntimeError("CP2K_Files directory not found.")

    if not job_body_file.is_file():
        raise RuntimeError("Missing cp2k_benchmarking_submit_include.txt")

    job_body = job_body_file.read_text().strip()
    benchmark_root.mkdir(exist_ok=True)

    jobs = []
    for total_cores in core_list:
        for ntasks, omp in mpi_openmp_permutations(total_cores):
            jobs.append((total_cores, ntasks, omp))

    print(f"\nGenerating {len(jobs)} benchmark configurations\n")

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

            # Memory policy
            if total_cores * mem_per_cpu_val > mem_floor_val:
                mem_line = f"#SBATCH --mem-per-cpu={mem_per_cpu}"
            else:
                mem_line = f"#SBATCH --mem={mem_floor}"

            # Time policy
            time_value = select_time(total_cores, time_policy)

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
    print("Time policy applied successfully.")