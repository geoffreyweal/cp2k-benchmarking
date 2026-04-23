import argparse
import csv
import json
import re
import subprocess
from pathlib import Path
from statistics import mean, stdev

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

def parse_nvt_ener_used_times(path: Path) -> list[float] | None:
    """
    Parse NVT1-1.ener and return a list of UsedTime[s] values
    excluding step 0. Returns None if file missing or no valid data.
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

            try:
                step = int(parts[0])
            except ValueError:
                continue

            try:
                used_time = float(parts[-1])
            except ValueError:
                continue

            if step == 0:
                continue

            used_times.append(used_time)

    if not used_times:
        return None

    return used_times


def parse_nvt_ener_stats(path: Path) -> tuple[float, float] | None:
    """
    Return (mean, stddev) of UsedTime[s] excluding step 0.
    Stddev is sample stddev if >=2 points, else 0.0.

    Returns None if file missing or no valid data.
    """
    vals = parse_nvt_ener_used_times(path)
    if not vals:
        return None

    mu = mean(vals)
    sigma = stdev(vals) if len(vals) >= 2 else 0.0
    return mu, sigma


# ------------------------------------------------------------
# SLURM sacct helpers (based on your algorithm, guarded)
# ------------------------------------------------------------

def run_sacct(jobid: str, taskid: str | None = None):
    """
    Run `sacct --json` for a job id (and optional task id).
    If taskid is supplied, query jobid_taskid, else query jobid.
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
        "avg_used_time_s", "avg_used_time_std_s",
        "jobid", "taskid",
        "sacct_elapsed_s", "sacct_cpu_s", "sacct_rss_gb",
        "cpu_eff_pct",
        "speedup",
    ]
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_skipped(skipped: list[tuple[str, str]], out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for d, reason in skipped:
            f.write(f"{d}\t{reason}\n")


# ------------------------------------------------------------
# Plotly plotting
# ------------------------------------------------------------

def _nearest_grid_surface(x, y, z, nx=35, ny=35):
    """
    Create a surface-like grid from scattered points using nearest-neighbour fill.
    No SciPy required.
    """
    import numpy as np

    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    z = np.asarray(z, dtype=float)

    xi = np.linspace(float(x.min()), float(x.max()), nx)
    yi = np.linspace(float(y.min()), float(y.max()), ny)
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
    3D + 2D interactive plots for ALL valid configurations.
    (Not filtered to fastest-per-cores.)
    """
    try:
        import plotly.graph_objects as go
    except Exception:
        print("NOTE: plotly not available; skipping interactive plots.")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    metrics = [
        ("avg_used_time_s", "Avg UsedTime (s)"),
        ("sacct_elapsed_s", "Elapsed time (s)"),
        ("sacct_cpu_s", "CPU time (s)"),
        ("sacct_rss_gb", "Max RSS (GB)"),
        ("cpu_eff_pct", "CPU efficiency (%)"),
        ("speedup", "Speedup (T1 / Tp)"),
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

        if len(pts) >= 6:
            try:
                X, Y, Z = _nearest_grid_surface(x, y, z, nx=35, ny=35)
                fig.add_trace(
                    go.Surface(
                        x=X, y=Y, z=Z,
                        opacity=0.35,
                        colorscale="Viridis",
                        showscale=False,
                        name="surface",
                    )
                )
            except Exception:
                pass

        out_html = out_dir / f"plot3d_{key}.html"
        fig.write_html(out_html, include_plotlyjs="cdn")
        print(f"  wrote {out_html}")

    # ---------- 2D plots with toggles + dropdown ----------
    unique_cores = sorted({r["total_cores"] for r in rows})

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

            xvals = [c] * len(group)
            yvals = [r[key] for r in group]
            text = [r["directory"] for r in group]
            mpi = [r["mpi_ranks"] for r in group]
            omp = [r["omp_threads"] for r in group]

            fig.add_trace(
                go.Scatter(
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
            )
            core_to_trace_index[c] = len(fig.data) - 1

        buttons = [{
            "label": "Show all cores",
            "method": "update",
            "args": [{"visible": [True] * len(fig.data)},
                     {"title": f"{label} vs Total cores (all groups)"}],
        }]

        for thr in unique_cores:
            vis = [False] * len(fig.data)
            for c in unique_cores:
                idx = core_to_trace_index.get(c)
                if idx is not None:
                    vis[idx] = (c >= thr)
            buttons.append({
                "label": f"Show cores ≥ {thr}",
                "method": "update",
                "args": [{"visible": vis},
                         {"title": f"{label} vs Total cores (cores ≥ {thr})"}],
            })

        fig.update_layout(
            title=f"{label} vs Total cores<br><sup>Toggle groups in legend or filter via dropdown</sup>",
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
# Best-per-total-cores 2x2 summary subplot (with stddev + toggle)
# ------------------------------------------------------------

def select_fastest_per_total_cores(rows: list[dict]) -> list[dict]:
    """
    For each total_cores, select the row with the smallest avg_used_time_s.
    Returns 1 row per total_cores (sorted).
    """
    best = {}
    for r in rows:
        t = r.get("avg_used_time_s")
        if t is None:
            continue
        c = r["total_cores"]
        if c not in best or t < best[c]["avg_used_time_s"]:
            best[c] = r
    return [best[c] for c in sorted(best.keys())]


def make_big_summary_subplot_fastest(rows: list[dict], out_dir: Path):
    """
    Big 2x2 subplot figure, ONLY using the fastest configuration per total_cores.
    Adds:
      - y-value + stddev in hover (where available)
      - error bars (stddev) with a toggle (default OFF)
      - CPU efficiency y-axis clamped 0..100
      - extra spacing so labels don't overlap titles
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except Exception:
        print("NOTE: plotly not available; skipping summary subplot.")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    best_rows = select_fastest_per_total_cores(rows)

    # Baseline for speedup (best 1-core)
    base_candidates = [
        r for r in best_rows
        if r["total_cores"] == 1 and r.get("avg_used_time_s") is not None
    ]
    base_time = min((r["avg_used_time_s"] for r in base_candidates), default=None)
    base_std = None
    if base_time is not None:
        # if multiple 1-core best_rows can't happen (best_rows has 1 per core),
        # but base_std comes from that single selected row
        for r in best_rows:
            if r["total_cores"] == 1:
                base_std = r.get("avg_used_time_std_s")
                break

    # Compute best speedup and its stddev (propagation) where possible
    for r in best_rows:
        t = r.get("avg_used_time_s")
        s = None
        s_std = None

        if base_time is not None and t is not None and t > 0:
            s = base_time / t

            # error propagation if stddevs exist
            t_std = r.get("avg_used_time_std_s")
            if (
                base_std is not None and t_std is not None
                and base_time > 0 and t > 0
            ):
                # σ_s = s * sqrt((σ_base/base)^2 + (σ_t/t)^2)
                s_std = s * (((base_std / base_time) ** 2 + (t_std / t) ** 2) ** 0.5)

        r["speedup_best"] = s
        r["speedup_best_std"] = s_std

    fig = make_subplots(
        rows=2,
        cols=2,
        specs=[[{"type": "xy"}, {"type": "xy"}],
               [{"type": "xy"}, {"type": "xy"}]],
        subplot_titles=[
            "CPU efficiency (%) (best per total cores)",
            "Max RSS (GB) (best per total cores)",
            "Average UsedTime (s) (best per total cores)",
            "Speedup T1/Tp (best per total cores)",
        ],
        vertical_spacing=0.12,
        horizontal_spacing=0.08,
    )

    # We'll keep track of trace indices so we can toggle error bars
    trace_indices = []

    def add_best_metric(row, col, key, ytitle, std_key=None, show_colorbar=False, y_range=None):
        pts = [r for r in best_rows if r.get(key) is not None]
        if not pts:
            return

        x = [r["total_cores"] for r in pts]
        y = [r[key] for r in pts]
        text = [r["directory"] for r in pts]
        mpi = [r["mpi_ranks"] for r in pts]
        omp = [r["omp_threads"] for r in pts]

        # stddev array (numeric) for error bars
        if std_key is not None:
            std_vals = [(r.get(std_key) or 0.0) for r in pts]
            std_strs = [
                (f"{r.get(std_key):.6g}" if r.get(std_key) is not None else "N/A")
                for r in pts
            ]
        else:
            std_vals = [0.0] * len(pts)
            std_strs = ["N/A"] * len(pts)

        marker = dict(
            size=8,
            color=mpi,
            colorscale="Viridis",
            showscale=show_colorbar,
        )
        if show_colorbar:
            marker["colorbar"] = dict(title="MPI ranks")

        tr = go.Scatter(
            x=x,
            y=y,
            mode="markers+lines",
            marker=marker,
            text=text,
            customdata=list(zip(mpi, omp, std_strs)),
            error_y=dict(
                type="data",
                array=std_vals,
                visible=False,   # default OFF (your request)
            ),
            hovertemplate=(
                "Dir: %{text}<br>"
                "Total cores: %{x}<br>"
                "MPI: %{customdata[0]}<br>"
                "OpenMP threads: %{customdata[1]}<br>"
                "Value: %{y}<br>"
                "Std dev: %{customdata[2]}<extra></extra>"
            ),
            showlegend=False,
        )

        fig.add_trace(tr, row=row, col=col)
        trace_indices.append(len(fig.data) - 1)

        fig.update_xaxes(title_text="Total cores", row=row, col=col)
        fig.update_yaxes(title_text=ytitle, row=row, col=col)

        if y_range is not None:
            fig.update_yaxes(range=y_range, row=row, col=col)

    # CPU efficiency: clamp 0..100, no stddev
    add_best_metric(
        1, 1,
        "cpu_eff_pct",
        "CPU efficiency (%)",
        std_key=None,
        show_colorbar=True,
        y_range=[0, 100],
    )

    # Memory usage: no stddev
    add_best_metric(1, 2, "sacct_rss_gb", "Max RSS (GB)", std_key=None)

    # Time: stddev from ener
    add_best_metric(2, 1, "avg_used_time_s", "Average UsedTime (s)", std_key="avg_used_time_std_s")

    # Speedup: stddev via propagation
    add_best_metric(2, 2, "speedup_best", "Speedup (T1/Tp)", std_key="speedup_best_std")

    # Toggle buttons for error bars across all traces
    ntraces = len(fig.data)
    fig.update_layout(
        updatemenus=[
            dict(
                type="buttons",
                direction="left",
                x=0.5,
                y=1.18,
                xanchor="center",
                yanchor="top",
                buttons=[
                    dict(
                        label="Error bars: OFF",
                        method="restyle",
                        args=[{"error_y.visible": [False] * ntraces}],
                    ),
                    dict(
                        label="Error bars: ON",
                        method="restyle",
                        args=[{"error_y.visible": [True] * ntraces}],
                    ),
                ],
            )
        ],
        title="CP2K Benchmark Summary (FASTEST per total cores)",
        height=950,
        margin=dict(l=70, r=40, t=140, b=70),
    )

    out_html = out_dir / "summary_2x2_best_per_total_cores.html"
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

    sim_dirs = [p for p in root.iterdir() if p.is_dir() and DIR_RE.match(p.name)]
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

        # Only include runs with valid ener stats
        stats = parse_nvt_ener_stats(ener_path)
        if stats is None:
            reason = f"missing {args.ener_file}" if not ener_path.is_file() else f"no usable UsedTime rows in {args.ener_file}"
            skipped.append((d.name, reason))
            continue

        avg_used_time, avg_used_time_std = stats

        m = DIR_RE.match(d.name)
        total_cores = int(m.group("cores"))
        mpi_ranks = int(m.group("mpi"))
        omp_threads = int(m.group("omp"))

        jobid, taskid = find_slurm_out_jobid(d)

        sacct_elapsed = sacct_cpu = sacct_rss = None
        cpu_eff = None

        if not args.no_sacct and jobid is not None:
            sacct_elapsed, sacct_cpu, sacct_rss = safe_sacct(jobid, taskid)

            # CPU efficiency (%) = cpu / (elapsed * cores) * 100
            if (
                sacct_elapsed is not None
                and sacct_cpu is not None
                and sacct_elapsed > 0
                and total_cores > 0
            ):
                cpu_eff = (sacct_cpu / (sacct_elapsed * total_cores)) * 100.0

        rows.append({
            "directory": d.name,
            "total_cores": total_cores,
            "mpi_ranks": mpi_ranks,
            "omp_threads": omp_threads,
            "avg_used_time_s": avg_used_time,
            "avg_used_time_std_s": avg_used_time_std,
            "jobid": jobid,
            "taskid": taskid,
            "sacct_elapsed_s": sacct_elapsed,
            "sacct_cpu_s": sacct_cpu,
            "sacct_rss_gb": sacct_rss,
            "cpu_eff_pct": cpu_eff,
            "speedup": None,  # filled below (per-row baseline speedup)
        })

    # Per-row speedup baseline: fastest 1-core mean time among ALL rows
    baseline_candidates = [r for r in rows if r["total_cores"] == 1 and r.get("avg_used_time_s") is not None]
    baseline_time = min((r["avg_used_time_s"] for r in baseline_candidates), default=None)
    if baseline_time is not None and baseline_time > 0:
        for r in rows:
            t = r.get("avg_used_time_s")
            if t is not None and t > 0:
                r["speedup"] = baseline_time / t

    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(rows, out_csv)
    write_skipped(skipped, skipped_txt)

    if rows:
        # Big 2x2: fastest-per-total-cores + stddev hover + toggleable error bars
        make_big_summary_subplot_fastest(rows, out_dir)

        # 3D/2D plots still show ALL configurations (useful for MPI/OMP tuning)
        make_plotly_plots(rows, out_dir)

    print("\nReport complete.")
    print(f"  Included runs (ener present): {len(rows)}")
    print(f"  Skipped runs (ener missing/invalid): {len(skipped)}")
    print(f"  CSV:   {out_csv}")
    print(f"  Skipped list: {skipped_txt}")
    print(f"  HTML plots directory: {out_dir}")
    print("  Open summary_2x2_best_per_total_cores.html for the best-per-cores summary.")