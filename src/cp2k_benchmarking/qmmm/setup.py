import argparse
import os
import shutil
from pathlib import Path


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

        start_str, end_str = range_part.split("-")
        start = int(start_str)
        end = int(end_str)

        for c in range(start, end + 1, step):
            cores.add(c)

    return sorted(cores)


def mpi_omp_permutations(total_cores: int):
    perms = []
    for ntasks in range(1, total_cores + 1):
        if total_cores % ntasks == 0:
            omp = total_cores // ntasks
            perms.append((ntasks, omp))
    return perms


def run():
    parser = argparse.ArgumentParser(
        description="Set up CP2K QM/MM benchmarking directories (MPI/OpenMP permutations)"
    )

    parser.add_argument(
        "--cores",
        required=True,
        help="Core specification, e.g. 8,12,16 or 1-32%2",
    )

    args = parser.parse_args()

    core_list = parse_cores(args.cores)

    source_cp2k_files = Path("CP2K_Files")
    benchmark_root = Path("CP2K_Benchmarking")
    job_body_file = Path("cp2k_benchmarking_submit_include.txt")

    if not source_cp2k_files.is_dir():
        raise RuntimeError("CP2K_Files directory not found.")

    if not job_body_file.is_file():
        raise RuntimeError(
            "Missing cp2k_benchmarking_submit_include.txt "
            "(this must contain the job body)."
        )

    job_body = job_body_file.read_text().strip()
    benchmark_root.mkdir(exist_ok=True)

    print("Generating MPI/OpenMP permutations:\n")

    for cores in core_list:
        perms = mpi_omp_permutations(cores)

        for ntasks, omp in perms:
            dirname = benchmark_root / f"{cores}C_{ntasks}M_{omp}T"

            if dirname.exists():
                shutil.rmtree(dirname)
            dirname.mkdir(parents=True)

            for item in source_cp2k_files.iterdir():
                dest = dirname / item.name
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)

            submit_file = dirname / "submit.sl"

            submit_file.write_text(f"""#!/bin/bash -e

#SBATCH --job-name=cp2k_qmmm_{cores}C_{ntasks}M_{omp}T
#SBATCH --ntasks={ntasks}
#SBATCH --cpus-per-task={omp}
#SBATCH --output=slurm_%j.out
#SBATCH --error=slurm_%j.err

export OMP_NUM_THREADS={omp}

{job_body}
""")

            os.chmod(submit_file, 0o755)

            print(f"  {dirname.name}")

    print("\nSetup complete.")
    print("One submit.sl created per MPI/OpenMP configuration.")
``