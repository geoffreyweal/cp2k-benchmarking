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

        start_str, end_str = range_part.split("-")
        start = int(start_str)
        end = int(end_str)

        for c in range(start, end + 1, step):
            cores.add(c)

    return sorted(cores)


def mpi_openmp_permutations(total_cores: int):
    """
    Generate all (ntasks, cpus-per-task) permutations
    such that ntasks * cpus-per-task = total_cores.
    """
    permutations = []
    for ntasks in range(1, total_cores + 1):
        if total_cores % ntasks == 0:
            omp = total_cores // ntasks
            permutations.append((ntasks, omp))
    return permutations


def run():
    parser = argparse.ArgumentParser(
        description="Set up CP2K QM/MM benchmarking directories "
                    "(MPI/OpenMP permutations)"
    )

    parser.add_argument(
        "--cores",
        required=True,
        help="Core specification, e.g. 8, 1-32%2, or 8,16,32",
    )

    args = parser.parse_args()

    core_list = parse_cores(args.cores)

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

    print("\nGenerating MPI/OpenMP benchmarking permutations\n")

    # -------------------------
    # Main generation loop
    # -------------------------

    for total_cores in tqdm(core_list, desc="Total core counts"):
        permutations = mpi_openmp_permutations(total_cores)

        for ntasks, omp in tqdm(
            permutations,
            desc=f"{total_cores} cores permutations",
            leave=False,
        ):
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

            submit_file = dirname / "submit.sl"

            submit_file.write_text(f"""#!/bin/bash -e

#SBATCH --job-name=cp2k_qmmm_{total_cores}C_{ntasks}MPI_{omp}OMP
#SBATCH --ntasks={ntasks}
#SBATCH --cpus-per-task={omp}
#SBATCH --output=slurm_%j.out
#SBATCH --error=slurm_%j.err

{job_body}
""")

            os.chmod(submit_file, 0o755)

    print("\nSetup complete.")
    print(
        "One submit.sl file has been created for each "
        "MPI/OpenMP configuration."
    )
``