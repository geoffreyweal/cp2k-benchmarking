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
# Directory / filename patterns
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

    Returns:
      - float average if valid rows exist (step > 0)
      - None if file is missing or contains no valid rows > 0
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

            # Step Nr is first column
            try:
                step = int(parts[0])
            except ValueError:
                continue

            # UsedTime[s] is last column
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
# SLURM sacct helpers (based on your algorithm)
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

    Uses your intended structure, but with defensive guards for
    site/version differences.
    """
    jobs = data.get("jobs", [])
    if not jobs:
        raise RuntimeError("sacct returned no jobs")

    job = jobs[0]
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


def safe_sacct(jobid: str | None, taskid: str | None):
    """
    Return (elapsed_s, cpu_s, rss_gb) or (None, None, None) if sacct fails.
    Tries jobid_taskid if taskid provided; otherwise tries jobid.
    """
    if not jobid:
        return None, None, None

    if taskid is not None:
        try:
            data = run_sacct(jobid, taskid)
            return parse_sacct_data(data)
        except Exception:
            pass

    try:
        data = run_sacct(jobid, None)
        return parse_sacct_data(data)
    except Exception:
        return None, None, None


# ------------------------------------------------------------
# slurm_*.out lookup
# ------------------------------------------------------------

def find_slurm_out_jobid(sim_dir: Path):
    """
    Find slurm_*.out in sim_dir and return (jobid, taskid) if found.
    Chooses the newest slurm_*.out if multiple exist.
    """
    candidates = sorted(
        sim_dir.glob("slurm_*.out"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in candidates:
        m = SLURM_OUT_RE.match(p.name)
        if m:
            return m.group("jobid"), m.group("taskid")
    return None, None


# ------------------------------------------------------------
# Output helpers
# ------------------------------------------------------------

def write_csv(rows: list[dict], out_csv: Path):
    out_csv.parent.mkdir(parents=True, exist_ok=True)
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


def write_skipped(skipped: list[tuple[str, str]], out_path: Path):
    """
    Write a list of (directory_name, reason) entries.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for d, reason in skipped:
            f.write(f"{d}\t{reason}\n")


# ------------------------------------------------------------
# Plotly plotting (interactive HTML)
# ------------------------------------------------------------

def _nearest_grid_surface(x, y, z, nx=35, ny=35):
    """
    Create a surface-like grid from scattered (x,y,z) points using
    nearest-neighbour assignment (no SciPy required).

    Returns (Xgrid, Ygrid, Zgrid) suitable for plotly go.Surface.
    """
    import numpy as np

    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    z = np.asarray(z, dtype=float)

    xmin, xmax = float(x.min()), float(x.max())
    ymin, ymax = float(y.min()), float(y.max())

    xi = np.linspace(xmin, xmax, nx)
    yi = np.linspace(ymin, ymax, ny)
    X, Y = np.meshgrid(xi, yi)

    Z = np.empty_like(X, dtype=float)
    for j in range(Y.shape[0]):
        for i in range(X.shape[1]):
            dx = x - X[j, i]
            dy = y - Y[j, i]
            k = int((dx * dx + dy * dy).argmin())
            Z[j, i] = z[k]

    return X, Y, Z


def make_plotly_plots(rows: list[dict], out_dir: Path):
    """
    Generate interactive Plotly HTML plots:

    - 3D: MPI vs OpenMP vs metric (scatter + optional surface-like overlay)
    - 2D: total_cores vs metric with:
          * legend toggles per total_cores group
          * dropdown filter to show only cores >= threshold
    """
    try:
        import plotly.graph_objects as go
    except Exception:
        print("NOTE: plotly not available; skipping interactive plots.")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    metrics = [
        ("avg_used_time_s", "Avg UsedTime (s) from NVT1-1.ener"),
        ("sacct_elapsed_s", "Elapsed time (s) from sacct"),
        ("sacct_cpu_s", "CPU time (s) from sacct"),
        ("sacct_rss_gb", "Max RSS (GB) from sacct"),
    ]

    # ---------- 3D plots ----------
    for key, label in metrics:
        pts = [r for r in rows if r.get(key) is not None]
        if len(pts) < 2:
            continue

        x = [r["mpi_ranks"] for r in pts]
        y = [r["omp_threads"] for r in pts]
        z = [r[key] for r in pts]
        cores = [r["total_cores"] for r in pts]
        names = [r["directory"] for r in pts]

        scatter = go.Scatter3d(
            x=x,
            y=y,
            z=z,
            mode="markers",
            marker=dict(
                size=5,
                color=cores,
                colorscale="Viridis",
                showscale=True,
                colorbar=dict(title="Total cores"),
            ),
            text=names,
            hovertemplate=(
                "Dir: %{text}<br>"
                "MPI: %{x}<br>"
                "OpenMP threads: %{y}<br>"
                f"{label}: %{z}<br>"
                "Total cores: %{marker.color}<extra></extra>"
            ),
            name="points",
        )

        fig = go.Figure(data=[scatter])
        fig.update_layout(
            title=f"{label}<br><sup>X=MPI ranks, Y=OpenMP threads, Z=metric</sup>",
            scene=dict(
                xaxis_title="MPI ranks",
                yaxis_title="OpenMP threads",
                zaxis_title=label,
            ),
            margin=dict(l=0, r=0, b=0, t=60),
        )

        # Surface-like overlay (optional)
        if len(pts) >= 6:
            try:
                X, Y, Z = _nearest_grid_surface(x, y, z, nx=35, ny=35)
                surface = go.Surface(
                    x=X,
                    y=Y,
                    z=Z,
                    opacity=0.35,
                    colorscale="Viridis",
                    showscale=False,
                    name="surface",
                )
                fig.add_trace(surface)
            except Exception:
                pass

        out_html = out_dir / f"plot3d_{key}.html"
        fig.write_html(out_html, include_plotlyjs="cdn")
        print(f"  wrote {out_html}")

    # ---------- 2D plots with toggles + dropdown ----------
    unique_cores = sorted({r["total_cores"] for r in rows})
    if not unique_cores:
        return

    for key, label in metrics:
        pts = [r for r in rows if r.get(key) is not None]
        if not pts:
            continue

        fig = go.Figure()
        core_to_trace_index = {}

        for c in unique_cores:
            group = [r for r in pts if r["total_cores"] == c]
            if not group:
                continue

            xvals = [c] * len(group)  # numeric x-axis
            yvals = [r[key] for r in group]
            text = [r["directory"] for r in group]
            mpi = [r["mpi_ranks"] for r in group]
            omp = [r["omp_threads"] for r in group]

            trace = go.Scatter(
                x=xvals,
                y=yvals,
                mode="markers",
                name=f"{c} cores",
                text=text,
                customdata=list(zip(mpi, omp)),
                hovertemplate=(
                    "Dir: %{text}<br>"
                    "Total cores: %{x}<br>"
                    "MPI: %{customdata[0]}<br>"
                    "OpenMP threads: %{customdata[1]}<br>"
                    f"{label}: %{y}<extra></extra>"
                ),
            )
            fig.add_trace(trace)
            core_to_trace_index[c] = len(fig.data) - 1

        buttons = []
        buttons.append(dict(
            label="Show all cores",
            method="update",
            args=[{"visible": [True] * len(fig.data)},
                  {"title": f"{label} vs Total cores (all groups)"}],
        ))

        for thr in unique_cores:
            full_vis = [False] * len(fig.data)
            for c in unique_cores:
                idx = core_to_trace_index.get(c)
                if idx is None:
                    continue
                full_vis[idx] = (c >= thr)

            buttons.append(dict(
                label=f"Show cores ≥ {thr}",
                method="update",
                args=[{"visible": full_vis},
                      {"title": f"{label} vs Total cores (cores ≥ {thr})"}],
            ))

        fig.update_layout(
            title=f"{label} vs Total cores<br><sup>Toggle groups in legend or use dropdown filter</sup>",
            xaxis_title="Total cores",
            yaxis_title=label,
            legend_title="Total cores group",
            updatemenus=[dict(
                type="dropdown",
                x=1.02, y=1.0,
                xanchor="left", yanchor="top",
                buttons=buttons,
            )],
            margin=dict(l=70, r=260, t=80, b=70),
        )

        out_html = out_dir / f"plot2d_{key}_by_total_cores.html"
        fig.write_html(out_html, include_plotlyjs="cdn")
        print(f"  wrote {out_html}")


# ------------------------------------------------------------
# Main report entry point
# ------------------------------------------------------------

def run():
    parser = argparse.ArgumentParser(
        description="Report CP2K benchmark results (interactive Plotly HTML plots)"
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
        help="Output directory for CSV and HTML plots (default: report)",
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
    skipped_txt = out_dir / "skipped_missing_ener.txt"

    if not root.is_dir():
        raise RuntimeError(f"Benchmark root not found: {root}")

    # Find matching benchmark directories
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
    skipped = []

    for d in iterator:
        ener_path = d / args.ener_file

        # ------------------------------------------------------------
        # NEW RULE: only include runs that have the .ener file
        # ------------------------------------------------------------
        if not ener_path.is_file():
            skipped.append((d.name, f"missing {args.ener_file}"))
            continue

        avg_used_time = parse_nvt_ener_avg_used_time(ener_path)

        # If file exists but contains no usable steps beyond 0, treat as failed too
        if avg_used_time is None:
            skipped.append((d.name, f"no usable UsedTime rows in {args.ener_file}"))
            continue

        m = DIR_RE.match(d.name)
        total_cores = int(m.group("cores"))
        mpi_ranks = int(m.group("mpi"))
        omp_threads = int(m.group("omp"))

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

    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(rows, out_csv)
    write_skipped(skipped, skipped_txt)

    # Plotly-only outputs (no matplotlib pngs)
    if rows:
        make_plotly_plots(rows, out_dir)

    print("\nReport complete.")
    print(f"  Included runs (ener present): {len(rows)}")
    print(f"  Skipped runs (ener missing/invalid): {len(skipped)}")
    print(f"  CSV:   {out_csv}")
    print(f"  Skipped list: {skipped_txt}")
    print(f"  HTML plots directory: {out_dir}")
    print("  Open the .html files in a browser (or via OOD file browser).")