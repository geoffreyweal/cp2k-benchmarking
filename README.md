# cp2k-benchmarking

A lightweight, policy-driven framework for **setting up and submitting CP2K benchmarking jobs on SLURM-based HPC systems**.

The design philosophy is inspired by practical large-scale benchmarking workflows:

- One directory per benchmark configuration
- Explicit, reproducible SLURM job scripts (`submit.sl`)
- Clear separation between **setup** and **submission**
- Transparent accounting of requested walltime

The tool is particularly well suited for **MPI/OpenMP scaling studies** and **QM/MM benchmarks**, but is intentionally general.

---

## Installation

Install directly from the Git repository using `pip`:

```bash
pip install git+https://github.com/geoffreyweal/cp2k-benchmarking
```

After installation, the command-line tool will be available as:

```bash
cp2k-benchmarking
```

---

## Required Directory Layout

Before running any commands, prepare the following files in your working directory:

### 1. `CP2K_Files/`

This directory should contain **everything needed to run a single CP2K job**, for example:

```text
CP2K_Files/
├── input.inp
├── BASIS_SET
├── POTENTIAL
└── ...
```

These files will be **copied verbatim** into every benchmark directory.

---

### 2. `cp2k_benchmarking_submit_include.txt`

This file contains the **job body** that will be appended to every generated `submit.sl` file.

It should include:
- Scheduler-specific directives (account, partition, etc., *except memory*)
- Module loads
- Environment variables
- The `srun` / `mpirun` command

Example:

```bash
#SBATCH --account=myproject
#SBATCH --partition=compute

module purge
module load CP2K/2024.1

export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK

srun cp2k.popt -i input.inp -o output.out
```

⚠️ **Do not include any `#SBATCH --mem*` directives here.**
Memory is controlled exclusively by `setup` policies.

---

## Command Overview

```text
cp2k-benchmarking qmmm setup   # Generate benchmark directories and submit scripts
cp2k-benchmarking submit       # Submit all generated jobs sequentially
```

---

## Benchmark Setup (`qmmm setup`)

### Basic Usage

```bash
cp2k-benchmarking qmmm setup --cores 1-8,10-16%2,20-32%4,40-166%8,166-332%8 --mem=128G --mem-per-cpu=2200MB --time-policy 30:00,15:00,10:00@16,64 --node-policy [l01],[l01,l02]@166
```

---

### Core Specification (`--cores`)

Controls the **total core counts** to benchmark. Each value is expanded into all valid MPI/OpenMP decompositions.

Examples:

```bash
--cores 8
--cores 1-16
--cores 1-32%2
--cores 8,16,32
```

For each total core count, all `(MPI × OpenMP)` factorizations are generated.

---

### Memory Policy

Two parameters control memory behavior:

```bash
--mem <minimum total memory>
--mem-per-cpu <memory per core>
```

The rule applied per job is:

```
If (total_cores × mem-per-cpu) > mem:
    use --mem-per-cpu
else:
    use --mem
```

Mixed units (e.g. `128G` and `2000M`) are supported and internally normalised.

---

### Time Policy (`--time-policy`)

Syntax:

```text
TIMES@THRESHOLDS
```

Example:

```text
30:00,15:00,10:00@16,64
```

Meaning:

| Total cores | Walltime |
|------------:|---------:|
| ≤ 16        | 30:00    |
| ≤ 64        | 15:00    |
| > 64        | 10:00    |

Supported formats: `MM:SS`, `HH:MM:SS`, `D-HH:MM:SS`.

---

### Node Policy (`--node-policy`)

Syntax:

```text
[NODES1],[NODES2]@THRESHOLD
```

Example:

```text
[l05],[l05,l06]@166
```

Meaning:

| Total cores | Nodes used |
|------------:|------------|
| ≤ 166       | l05        |
| > 166       | l05,l06    |

This generates both:

```bash
#SBATCH --nodes=<N>
#SBATCH --nodelist=<list>
```

---

### Output of `setup`

- Creates `CP2K_Benchmarking/`
- One directory per MPI/OpenMP configuration:

```text
CP2K_Benchmarking/
└── 32_Cores_8_MPI_4_OpenMPI/
    ├── submit.sl
    ├── input.inp
    └── ...
```

- Displays a progress bar
- Prints the **total requested walltime across all jobs**, e.g.:

```text
Total requested walltime across all jobs: 5d 03:40:00 (460,800 seconds)
```

---

## Job Submission (`submit`)

### Basic Usage

```bash
cd CP2K_Benchmarking
cp2k-benchmarking submit
```

The submission command:

- Recursively finds all `submit.sl`
- Prints total requested walltime
- Submits jobs one-by-one
- Pauses briefly every 10 submissions to avoid QOS bursts
- Continues even if some submissions fail

---

### Common Options

```bash
--dry-run     # Show what would be submitted
--yes         # Skip confirmation prompt
--root PATH   # Directory to search (default: .)
```

Example:

```bash
cp2k-benchmarking submit --dry-run
```

Example output:

```text
Found 24 submit.sl files
Total requested walltime: 6d 02:30:00 (529,200 seconds)
```

---

## Reporting and Analysis (cp2k-benchmarking qmmm report)

The `report` subcommand analyses completed CP2K benchmark runs and produces **interactive, publication‑quality performance summaries**. It is designed to work directly on the directory structure created by `qmmm setup` and intentionally ignores failed or incomplete runs.

```Shell
cp2k-benchmarking qmmm report [options]
```

options:

- `--root PATH`
    - Root benchmarking directory (default: `CP2K_Benchmarking`)
- `--ener-file FILE`
    - Energy file to parse (default: `NVT1-1.ener`)
- `--out DIR`
    - Output directory for CSV and plots (default: `report`)
- `--no-sacct`
    - Skip SLURM accounting queries

---

### What the report does

When you run `cp2k-benchmarking qmmm report`, the tool performs the following steps:

#### 1. Scan benchmark directories

- Searches under `CP2K_Benchmarking/` (or `--root PATH` if specified)
- Only considers directories matching the naming scheme:

```PlainText
<TOTAL>_Cores_<MPI>_MPI_<OMP>_OpenMPI
```

This naming convention is required so that the tool can infer:

- total core count
- MPI ranks
- OpenMP threads

---

#### 2. Filter out failed runs (strict by design)

A run is considered **valid only if** it contains a usable CP2K energy file:

```PlainText
NVT1-1.ener
```

Rules:

- The file must exist
- It must contain at least one timestep **after step 0**
- Runs that do not meet these conditions are **excluded entirely**

Excluded runs are recorded in:

```PlainText
report/skipped_missing_ener.txt
```

This ensures that all reported metrics correspond to *completed and meaningful* CP2K calculations.

---

#### 3. Extract CP2K timing statistics

From `NVT1-1.ener` the report computes:

- **Average** `UsedTime[s]`, excluding step 0
- **Standard deviation** of `UsedTime[s]`

These values are used as the primary performance metric throughout the report.

---

#### 4. Extract SLURM accounting data (optional)

If available, SLURM accounting data is queried using:

```Shell
sacct --json
```

The job ID is inferred from `slurm_*.out` files in each benchmark directory.

Extracted quantities:

- Elapsed wall time (s)
- Total CPU time (s)
- Maximum resident set size (RSS, GB)

You can disable SLURM queries if `sacct` is unavailable:

```Shell
cp2k-benchmarking qmmm report --no-sacct
```

---

#### 5. Compute derived performance metrics

##### CPU efficiency (%)

```PlainText
CPU efficiency = CPU_time / (elapsed_time × total_cores) × 100
```

This value is clamped to the range **0–100%** in plots.

##### Speedup

```PlainText
Speedup(p) = T₁ / Tₚ
```

Where:

- `T₁` is the **fastest valid 1‑core run** (minimum average `UsedTime[s]`)
- `Tₚ` is the average `UsedTime[s]` at `p` total cores

For speedup, a standard deviation is computed using standard error propagation when timing uncertainties are available.

---

### Output files

All outputs are written to the directory specified by `--out` (default: `report/`).

#### Machine‑readable summary

```PlainText
report/results.csv
```

Contains one row per **valid benchmark configuration**, including:

- core count, MPI ranks, OpenMP threads
- average UsedTime and standard deviation
- SLURM elapsed time, CPU time, RSS
- CPU efficiency
- speedup

---

### Interactive plots (HTML)

All plots are written as **self‑contained HTML files**. No server is required — open them directly in a browser or via Open OnDemand’s file browser.

---

#### 1. Big 2×2 summary plot (best‑per‑cores)

```PlainText
report/summary_2x2_best_per_total_cores.html
```

This is the **primary overview figure** and shows **only the fastest configuration for each total core count**.

Fastest is defined as:

```PlainText
minimum average UsedTime[s] from NVT1-1.ener
```

##### Subplots

1. **CPU efficiency (%)**
    - Limited to the range 0–100%
2. **Maximum RSS (GB)**
3. **Average UsedTime (s)**
4. **Speedup (T₁ / Tₚ)**

##### Additional features

- Hover tooltips show:
    - directory name
    - total cores
    - MPI ranks
    - OpenMP threads
    - metric value
    - standard deviation (when available)
- **Error bars (±1σ)**
    - Available for average UsedTime and speedup
    - Controlled by an **Error bars ON/OFF** toggle
    - **Disabled by default**
- **Theoretical reference lines**:
    - Average time: `T₁ / cores`
    - Speedup: ideal linear scaling `y = x`
    - These reference lines never display error bars

This plot is intended for **scaling analysis and presentation‑quality summaries**.

---

#### 2. 3D interactive plots (MPI × OpenMP × metric)

For each metric, a 3D interactive plot is generated:

```PlainText
report/plot3d_<metric>.html
```

Axes:

- **X**: MPI ranks
- **Y**: OpenMP threads
- **Z**: metric value

Features:

- Scatter points for all valid configurations
- Optional surface‑like overlay (nearest‑neighbour interpolation)
- Colour‑mapped by total core count

These plots are particularly useful for exploring **MPI/OpenMP decomposition effects**.

---

#### 3. 2D interactive plots (all configurations)

For each metric:

```PlainText
report/plot2d_<metric>_by_total_cores.html
```

Features:

- X‑axis: total cores
- One trace per total core count
- Legend toggles to enable/disable specific core groups
- Dropdown filter to show only configurations with cores ≥ a selected threshold

These plots show *all* configurations (not just the fastest) and are useful for tuning studies.

---

## Typical workflow

```Shell
# Generate benchmark jobs
cp2k-benchmarking qmmm setup ...

# Submit jobs
cp2k-benchmarking submit

# Analyse results
cp2k-benchmarking qmmm report

# Open the main summary plot
firefox report/summary_2x2_best_per_total_cores.html
```

---

## Design philosophy

The reporting stage is intentionally:

- **Strict** – failed or incomplete runs are excluded
- **Reproducible** – all results are derived from files on disk
- **Transparent** – raw CSV output is always written
- **Interactive** – HTML plots support hover, toggles, and filtering

This makes `qmmm report` suitable for:

- scaling studies
- MPI/OpenMP decomposition analysis
- performance regression testing
- HPC support and documentation


---

## License

MIT License

---

## Acknowledgements

Inspired by practical HPC benchmarking workflows and the structure of:

- https://github.com/geoffreyweal/orca-benchmarking
