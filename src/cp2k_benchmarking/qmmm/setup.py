import argparse
import shutil
from pathlib import Path

from tqdm import tqdm


# =================================================
# Core parsing
# =================================================

def parse_cores(core_string: str) -> list:
    cores = set()
    for block in core_string.split(","):
        block = block.strip()
        if "-" not in block:
            cores.add(int(block))
            continue

        if "%" in block:
            rng, step = block.split("%")
            step = int(step)
        else:
            rng = block
            step = 1

        start, end = map(int, rng.split("-"))
        for c in range(start, end + 1, step):
            cores.add(c)

    return sorted(cores)


def mpi_openmp_permutations(total_cores: int):
    return [
        (ntasks, total_cores // ntasks)
        for ntasks in range(1, total_cores + 1)
        if total_cores % ntasks == 0
    ]


# =================================================
# Memory handling (MB-normalised)
# =================================================

def parse_mem_value(mem_str: str) -> float:
    mem_str = mem_str.strip().upper()

    if mem_str.endswith(("GB", "G")):
        return float(mem_str.rstrip("GB").rstrip("G")) * 1024
    if mem_str.endswith(("MB", "M")):
        return float(mem_str.rstrip("MB").rstrip("M"))

    raise ValueError(f"Unrecognised memory format: {mem_str}")


# =================================================
# Time policy + accounting
# =================================================

def parse_time_policy(policy: str):
    # Example: 30:00,15:00,10:00@16,64
    if "@" not in policy:
        raise ValueError("Invalid time policy format")

    times_part, thresholds_part = policy.split("@", 1)
    times = [t.strip() for t in times_part.split(",")]
    thresholds = [int(x.strip()) for x in thresholds_part.split(",")]

    if len(times) != len(thresholds) + 1:
        raise ValueError("Time policy must have N+1 times for N thresholds")

    return times, thresholds


def select_time(total_cores: int, times, thresholds) -> str:
    for time_str, max_cores in zip(times, thresholds):
        if total_cores <= max_cores:
            return time_str
    return times[-1]


def parse_slurm_time_to_seconds(time_str: str) -> int:
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
    days, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)

    if days > 0:
        return f"{days}d {h:02d}:{m:02d}:{s:02d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


# =================================================
# Node policy
# =================================================

def parse_node_policy(policy: str):
    # Example: [l05],[l05,l06]@166
    nodesets_part, threshold_part = policy.split("@", 1)
    threshold = int(threshold_part.strip())

    raw_sets = nodesets_part.split("],")
    node_sets = []

    for s in raw_sets:
        s = s.strip().lstrip("[").rstrip("]")
        node_sets.append([n.strip() for n in s.split(",") if n.strip()])

    if len(node_sets) != 2:
        raise ValueError("Exactly two node sets must be provided")

    return node_sets[0], node_sets[1], threshold


def select_nodes(total_cores: int, low_nodes, high_nodes, threshold):
    return low_nodes if total_cores <= threshold else high_nodes


# =================================================
# Main
# =================================================

def run():
    parser = argparse.ArgumentParser("CP2K benchmarking setup")

    parser.add_argument("--cores", required=True)
    parser.add_argument("--mem", required=True)
    parser.add_argument("--mem-per-cpu", required=True)
    parser.add_argument("--time-policy", required=True)
    parser.add_argument("--node-policy", required=True)

    args = parser.parse_args()

    core_list = parse_cores(args.cores)
    times, time_thresholds = parse_time_policy(args.time_policy)
    low_nodes, high_nodes, node_threshold = parse_node_policy(args.node_policy)

    mem_floor_val = parse_mem_value(args.mem)
    mem_cpu_val = parse_mem_value(args.mem_per_cpu)

    bench_root = Path("CP2K_Benchmarking")
    bench_root.mkdir(exist_ok=True)

    src_files = Path("CP2K_Files")
    job_body = Path("cp2k_benchmarking_submit_include.txt").read_text().strip()

    jobs = []
    for c in core_list:
        for nt, omp in mpi_openmp_permutations(c):
            jobs.append((c, nt, omp))

    total_requested_seconds = 0

    with tqdm(total=len(jobs), desc="Creating benchmark configurations") as pbar:
        for total_cores, ntasks, omp in jobs:
            nodes = select_nodes(
                total_cores, low_nodes, high_nodes, node_threshold
            )

            dirname = bench_root / f"{total_cores}_Cores_{ntasks}_MPI_{omp}_OpenMPI"
            if dirname.exists():
                shutil.rmtree(dirname)
            dirname.mkdir(parents=True)

            for f in src_files.iterdir():
                shutil.copytree(f, dirname / f.name, dirs_exist_ok=True) if f.is_dir() else shutil.copy2(f, dirname / f.name)

            # Memory policy
            if total_cores * mem_cpu_val > mem_floor_val:
                mem_line = f"#SBATCH --mem-per-cpu={args.mem_per_cpu}"
            else:
                mem_line = f"#SBATCH --mem={args.mem}"

            time_value = select_time(total_cores, times, time_thresholds)
            total_requested_seconds += parse_slurm_time_to_seconds(time_value)

            submit = dirname / "submit.sl"
            submit.write_text(f"""#!/bin/bash -e

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
    print(f"Total requested walltime across all jobs: "
          f"{format_seconds(total_requested_seconds)} "
          f"({total_requested_seconds:,} seconds)")
