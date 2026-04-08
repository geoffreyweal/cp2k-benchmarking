import argparse
import os
import shutil
from pathlib import Path


def parse_cores(core_string: str) -> list:
    """
    Parse a core specification string.

    Examples
    --------
    1-8             -> [1,2,3,4,5,6,7,8]
    10-16%2         -> [10,12,14,16]
    1-8,10-16%2     -> [1,2,3,4,5,6,7,8,10,12,14,16]
    """
    cores = set()

    for block in core_string.split(","):
        block = block.strip()

        # Single value
        if "-" not in block:
            cores.add(int(block))
            continue

        # Range or stepped range
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


def run():
    parser = argparse.ArgumentParser(
        description="Set up CP2K QM/MM benchmarking directories"
    )

    parser.add_argument(
        "--cores",
        required=True,
        help="Core specification, e.g. 1-8,10-16%2,20-32%4",
    )

    parser.add_argument(
        "--mem-per-cpu",
        default="2000MB",
        help="Memory per CPU for SLURM, e.g. 2000MB, 2G (default: 2000MB)",
    )

    args = parser.parse_args()

    ntasks_list = parse_cores(args.cores)
    mem_per_cpu = args.mem_per_cpu

    print(f"Benchmarking cores: {ntasks_list}")
    print(f"Memory per CPU:     {mem_per_cpu}")

    # Paths
    source_cp2k_files = Path("CP2K_Files")
    benchmark_root = Path("CP2K_Benchmarking")
    include_file = Path("cp2k_benchmarking_submit_include.txt")

    # Sanity checks
    if not source_cp2k_files.is_dir():
        raise RuntimeError(
            "CP2K_Files directory not found.\n"
            "It must contain run_nvt.sh and CP2K inputs."
        )

    if not include_file.is_file():
        raise RuntimeError(
            "Missing cp2k_benchmarking_submit_include.txt\n"
            "This file must contain site-specific #SBATCH directives."
        )

    include_text = include_file.read_text().strip()

    benchmark_root.mkdir(exist_ok=True)

    # -----------------------------------
    # Create per-core benchmark directories
    # -----------------------------------

    for ntasks in ntasks_list:
        core_dir = benchmark_root / f"{ntasks}cores"

        if core_dir.exists():
            shutil.rmtree(core_dir)

        core_dir.mkdir(parents=True)

        # Copy *contents* of CP2K_Files into XXXcores/
        for item in source_cp2k_files.iterdir():
            dest = core_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        print(f"Prepared: {core_dir}")

    # -----------------------------------
    # Write SLURM job array script
    # -----------------------------------

    array_list = ",".join(str(n) for n in ntasks_list)
    submit_array = benchmark_root / "submit_array.sl"

    submit_array.write_text(f"""#!/bin/bash
{include_text}

#SBATCH --job-name=cp2k_qmmm
#SBATCH --mem-per-cpu={mem_per_cpu}
#SBATCH --array={array_list}
#SBATCH --output=slurm_%A_%a.out
#SBATCH --error=slurm_%A_%a.err

set -e

NTASKS=$SLURM_ARRAY_TASK_ID

cd CP2K_Benchmarking/${{NTASKS}}cores
bash run_nvt.sh
""")

    os.chmod(submit_array, 0o755)

    print("\nSetup complete.")
    print("Submit benchmarks with:")
    print("  cd CP2K_Benchmarking")
    print("  sbatch submit_array.sl")