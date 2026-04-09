import argparse
import shutil
from pathlib import Path

from tqdm import tqdm


# -------------------------------------------------
# Parsing utilities
# -------------------------------------------------

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


# -------------------------------------------------
# Memory (MB-normalised)
# -------------------------------------------------

def parse_mem_value(mem_str: str) -> float:
    mem_str = mem_str.strip().upper()

    if mem_str.endswith("GB") or mem_str.endswith("G"):
        return float(mem_str.rstrip("GB").rstrip("G")) * 1024

    if mem_str.endswith("MB") or mem_str.endswith("M"):
        return float(mem_str.rstrip("MB").rstrip("M"))

    raise ValueError(
        f"Unrecognised memory format: {mem_str}. "
        "Use M/MB or G/GB."
    )


# -------------------------------------------------
# Time policy
# -------------------------------------------------

def parse_time_policy(policy: str):
    policy = policy.strip()

    if "@" not in policy:
        raise ValueError("Invalid time policy format")

    times_part, thresholds_part = policy.split("@", 1)
    times = [t.strip() for t in times_part.split(",")]
    thresholds = [int(c.strip()) for c in thresholds_part.split(",")]

    if len(times) != len(thresholds) + 1:
        raise ValueError("Invalid time policy")

    return times, thresholds


def select_time(total_cores: int, times, thresholds) -> str:
    for time_str, max_cores in zip(times, thresholds):
        if total_cores <= max_cores:
            return time_str
    return times[-1]


# -------------------------------------------------
# Node policy
# -------------------------------------------------

def parse_node_policy(policy: str):
    policy = policy.strip()

    if "@" not in policy:
        raise ValueError(
            "Node policy must be NODESETS@THRESHOLD "
            "(e.g. [l05],[l05,l06]@166)"
        )

    nodesets_part, threshold_part = policy.split("@", 1)
    threshold = int(threshold_part.strip())

    raw_sets = nodesets_part.split("],")
    node_sets = []

    for s in raw_sets:
        s = s.strip().lstrip("[").rstrip("]")
        nodes = [n.strip() for n in s.split(",") if n.strip()]
        node_sets.append(nodes)

    if len(node_sets) != 2:
        raise ValueError("Exactly two node sets must be supplied")

    return node_sets[0], node_sets[1], threshold


def select_nodes(total_cores: int, low_nodes, high_nodes, threshold):
    if total_cores <= threshold:
        return low_nodes
    return high_nodes


# -------------------------------------------------
# Main
# -------------------------------------------------

def run():
    parser = argparse.ArgumentParser(
        description="CP2K benchmarking setup with core, memory, time, and node policies"
    )

    parser.add_argument("--cores", required=True)
    parser.add_argument("--mem", required=True)
    parser.add_argument("--mem-per-cpu", required=True)
    parser.add_argument("--time-policy", required=True)
    parser.add_argument("--node-policy", required=True)

    args = parser.parse_args()

    core_list = parse_cores(args.cores)

    mem_floor = args.mem
    mem_per_cpu = args.mem_per_cpu
    mem_floor_val = parse_mem_value(mem_floor)
    mem_per_cpu_val = parse_mem_value(mem_per_cpu)

    times, thresholds = parse_time_policy(args.time_policy)

    low_nodes, high_nodes, node_threshold = parse_node_policy(args.node_policy)

    source_cp2k_files = Path("CP2K_Files")
    benchmark_root = Path("CP2K_Benchmarking")
    job_body = Path("cp2k_benchmarking_submit_include.txt").read_text().strip()

    benchmark_root.mkdir(exist_ok=True)

    jobs = []
    for total_cores in core_list:
        for ntasks, omp in mpi_openmp_permutations(total_cores):
            jobs.append((total_cores, ntasks, omp))

    with tqdm(total=len(jobs), desc="Creating benchmark configurations") as pbar:
        for total_cores, ntasks, omp in jobs:
            nodes = select_nodes(
                total_cores, low_nodes, high_nodes, node_threshold
            )

            dirname = benchmark_root / f"{total_cores}_Cores_{ntasks}_MPI_{omp}_OpenMPI"
            dirname.mkdir(parents=True, exist_ok=True)

            for item in source_cp2k_files.iterdir():
                dest = dirname / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)

            if total_cores * mem_per_cpu_val > mem_floor_val:
                mem_line = f"#SBATCH --mem-per-cpu={mem_per_cpu}"
            else:
                mem_line = f"#SBATCH --mem={mem_floor}"

            time_value = select_time(total_cores, times, thresholds)

            submit_file = dirname / "submit.sl"
            submit_file.write_text(f"""#!/bin/bash -e

#SBATCH --job-name=cp2k_qmmm_{total_cores}C_{ntasks}MPI_{omp}OMP
#SBATCH --ntasks={ntasks}
#SBATCH --cpus-per-task={omp}
#SBATCH --nodes={len(nodes)}
#SBATCH --nodelist={",".join(nodes)}
#SBATCH --time={time_value}
{mem_line}
#SBATCH --output=slurm_%j.out
#SBATCH --error=slurm_%j.err

{job_body}
""")

            pbar.update(1)

    print("\nSetup complete.")
