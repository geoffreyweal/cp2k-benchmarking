import argparse
import csv
import json
import re
import subprocess
from pathlib import Path
from statistics import mean

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover
    tqdm = None


# ------------------------------------------------------------
# Directory name parsing
# ------------------------------------------------------------

DIR_RE = re.compile(
    r"^(?P<cores>\d+)_Cores_(?P<mpi>\d+)_MPI_(?P<omp>\d+)_OpenMPI$"
)

SLURM_OUT_RE = re.compile(r"^slurm_(?P<jobid>\d+)(?:_(?P<taskid>\d+))?\.out$")


# ------------------------------------------------------------
# NVT1-1.ener parsing
# ------------------------------------------------------------

def parse_nvt_ener_avg_used_time(path: Path) -> float | None:
    """
    Parse NVT1-1.ener and return average UsedTime[s] excluding step 0.
    Returns None if file missing or no valid rows.
    """
    if not path.is_file():
        return None

    used_times = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split()
            if len(parts) < 2:
                continue

            # Expect first column is step number
            try:
                step = int(parts[0])
            except ValueError:
                continue

            # UsedTime[s] is the last column
            try:
                used_time = float(parts[-1])
            except ValueError:
                continue

            # Skip step 0 explicitly
            if step == 0:
                continue

            used_times.append(used_time)

    if not used_times:
        return None

    return mean(used_times)


# ------------------------------------------------------------
# SLURM sacct helpers (your logic, made robust)
# ------------------------------------------------------------

def run_sacct(jobid: str, taskid: str | None = None):
    """
    Run `sacct --json` for a job id (and optional task id).
    If taskid is supplied, query jobid_taskid, else query jobid.

    Returns parsed JSON (dict).
    """
    job_query = f"{jobid}_{taskid}" if taskid is not None else f"{jobid}"

    result = subprocess.run(
        ["sacct", "--json", "-j", job_query],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def parse_sacct_data(data):
    """
    Extract from sacct JSON:
      * elapsed time (s)
      * total CPU time (s)
      * maximum RSS (GB)

    This uses the structure you provided, but with guards so it doesn't crash
    if some keys are absent on certain clusters or sacct versions.
    """
    jobs = data.get("jobs", [])
    if not jobs:
        raise RuntimeError("sacct returned no jobs")

    job = jobs[0]

    # elapsed seconds
    elapsed = float(job.get("time", {}).get("elapsed", 0.0))

    cpu_msec = 0
    max_mem_b = 0

    for step in job.get("steps", []):
        tres = step.get("tres", {})
        requested = tres.get("requested", {})
        total = requested.get("total", [])

        for t in total:
            ttype = t.get("type")
            count = t.get("count", 0)

            if ttype == "cpu":
                cpu_msec += count
            elif ttype == "mem":
                max_mem_b = max(max_mem_b, count)

    max_mem_gb = max_mem_b / 1024.0 / 1024.0 / 1024.0
    return elapsed, cpu_msec / 1000.0, max_mem_gb


# ------------------------------------------------------------
# Utilities
# ------------------------------------------------------------

def find_slurm_out_jobid(sim_dir: Path):
    """
    Find slurm_*.out in sim_dir and return (jobid, taskid) if found.
    Chooses the newest slurm_*.out if multiple exist.
    """
    candidates = sorted(sim_dir.glob("slurm_*.out"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in candidates:
        m = SLURM_OUT_RE.match(p.name)
        if m:
            return m.group("jobid"), m.group("taskid")
    return None, None


def safe_sacct(jobid: str | None, taskid: str | None):
    """
    Return (elapsed_s, cpu_s, rss_gb) or (None, None, None) if sacct fails.
    Tries jobid_taskid if taskid provided; otherwise tries jobid.
    """
    if not jobid:
        return None, None, None

    # Try with taskid if available
    if taskid is not None:
        try:
            data = run_sacct(jobid, taskid)
            return parse_sacct_data(data)
        except Exception:
            pass

    # Fallback: try jobid alone
    try:
        data = run_sacct(jobid, None)
        return parse_sacct_data(data)
    except Exception:
        return None, None, None


def write_csv(rows: list[dict], out_csv: Path):
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        # write header only
        with out_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "directory", "total_cores", "mpi_ranks", "omp_threads",
                "avg_used_time_s", "jobid", "taskid",
                "sacct_elapsed_s", "sacct_cpu_s", "sacct_rss_gb"
            ])
        return

    fieldnames = [
        "directory", "total_cores", "mpi_ranks", "omp_threads",
        "avg_used_time_s", "jobid", "taskid",
        "sacct_elapsed_s", "sacct_cpu_s", "sacct_rss_gb"
    ]
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def make_plots(rows: list[dict], out_dir: Path):
    """
    Create simple matplotlib plots. If matplotlib is missing,
    print a message and skip plotting.
    """
    try:
        import matplotlib.pyplot as plt
    except Exception:
        print("NOTE: matplotlib not available; skipping plots.")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    # Helper to extract data points where y is present
    def points(ykey):
        xs, ys, labels = [], [], []
        for r in rows:
            y = r.get(ykey)
            if y is None:
                continue
            xs.append(r["total_cores"])
            ys.append(y)
            labels.append(r["directory"])
        return xs, ys, labels

    # 1) Avg UsedTime vs cores
    xs, ys, _ = points("avg_used_time_s")
    if xs:
        plt.figure()
        plt.scatter(xs, ys)
        plt.xlabel("Total cores")
        plt.ylabel("Average UsedTime (s) from NVT1-1.ener")
        plt.title("CP2K: Average UsedTime vs Total cores")
        plt.savefig(out_dir / "avg_used_time_vs_cores.png", dpi=200, bbox_inches="tight")
        plt.close()

    # 2) sacct elapsed vs cores
    xs, ys, _ = points("sacct_elapsed_s")
    if xs:
        plt.figure()
        plt.scatter(xs, ys)
        plt.xlabel("Total cores")
        plt.ylabel("Elapsed time (s) from sacct")
        plt.title("SLURM sacct: Elapsed vs Total cores")
        plt.savefig(out_dir / "sacct_elapsed_vs_cores.png", dpi=200, bbox_inches="tight")
        plt.close()

    # 3) sacct cpu vs cores
    xs, ys, _ = points("sacct_cpu_s")
    if xs:
        plt.figure()
        plt.scatter(xs, ys)
        plt.xlabel("Total cores")
        plt.ylabel("CPU time (s) from sacct")
        plt.title("SLURM sacct: CPU time vs Total cores")
        plt.savefig(out_dir / "sacct_cpu_vs_cores.png", dpi=200, bbox_inches="tight")
        plt.close()

    # 4) sacct RSS vs cores
    xs, ys, _ = points("sacct_rss_gb")
    if xs:
        plt.figure()
        plt.scatter(xs, ys)
        plt.xlabel("Total cores")
        plt.ylabel("Max RSS (GB) from sacct")
        plt.title("SLURM sacct: Max RSS vs Total cores")
        plt.savefig(out_dir / "sacct_rss_gb_vs_cores.png", dpi=200, bbox_inches="tight")
        plt.close()


# ------------------------------------------------------------
# Main report entry point
# ------------------------------------------------------------

def run():
    parser = argparse.ArgumentParser(
        description="Report CP2K benchmark results from benchmark directories"
    )
    parser.add_argument(
        "--root",
        default="CP2K_Benchmarking",
        help="Root benchmarking directory (default: CP2K_Benchmarking)",
    )
    parser.add_argument(
        "--ener-file",
        default="NVT1-1.ener",
        help="Energy file to parse (default: NVT1-1.ener)",
    )
    parser.add_argument(
        "--out",
        default="report",
        help="Output directory for CSV and plots (default: report)",
    )
    parser.add_argument(
        "--no-sacct",
        action="store_true",
        help="Skip calling sacct (useful if sacct unavailable)",
    )

    args = parser.parse_args()

    root = Path(args.root).resolve()
    out_dir = Path(args.out).resolve()
    out_csv = out_dir / "results.csv"

    if not root.is_dir():
        raise RuntimeError(f"Benchmark root not found: {root}")

    sim_dirs = []
    for p in root.iterdir():
        if p.is_dir() and DIR_RE.match(p.name):
            sim_dirs.append(p)

    sim_dirs = sorted(sim_dirs, key=lambda p: p.name)

    if not sim_dirs:
        print(f"No benchmark directories found under: {root}")
        return

    iterator = sim_dirs
    if tqdm is not None:
        iterator = tqdm(sim_dirs, desc="Scanning benchmark directories")

    rows = []
    for d in iterator:
        m = DIR_RE.match(d.name)
        total_cores = int(m.group("cores"))
        mpi_ranks = int(m.group("mpi"))
        omp_threads = int(m.group("omp"))

        avg_used_time = parse_nvt_ener_avg_used_time(d / args.ener_file)

        jobid, taskid = find_slurm_out_jobid(d)

        sacct_elapsed = sacct_cpu = sacct_rss = None
        if not args.no_sacct and jobid is not None:
            sacct_elapsed, sacct_cpu, sacct_rss = safe_sacct(jobid, taskid)

        rows.append({
            "directory": d.name,
            "total_cores": total_cores,
            "mpi_ranks": mpi_ranks,
            "omp_threads": omp_threads,
            "avg_used_time_s": avg_used_time,
            "jobid": jobid,
            "taskid": taskid,
            "sacct_elapsed_s": sacct_elapsed,
            "sacct_cpu_s": sacct_cpu,
            "sacct_rss_gb": sacct_rss,
        })

    write_csv(rows, out_csv)
    make_plots(rows, out_dir)

    print("\nReport complete.")
    print(f"  CSV : {out_csv}")
    print(f"  Plots directory: {out_dir}")
    print("  Plots written (if matplotlib available):")
    for name in [
        "avg_used_time_vs_cores.png",
        "sacct_elapsed_vs_cores.png",
        "sacct_cpu_vs_cores.png",
        "sacct_rss_gb_vs_cores.png",
    ]:
        p = out_dir / name
        if p.exists():
            print(f"    - {p}")