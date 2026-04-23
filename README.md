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

## Reporting and Analysis (`cp2k-benchmarking qmmm report`)

The `report` subcommand analyses completed CP2K benchmark runs and produces **interactive, publication‑quality performance summaries**. It is designed to work directly on the directory structure created by `qmmm setup` and intentionally ignores failed or incomplete runs.

```bash
cp2k-benchmarking qmmm report
```

---

### What the report does

When you run `cp2k-benchmarking qmmm report`, the tool performs the following steps:

#### 1. Scan benchmark directories

- Searches under `CP2K_Benchmarking/` (or `--root PATH` if specified)
- Only considers directories matching the naming scheme:

```text
<TOTAL>_Cores_<MPI>_MPI_<OMP>_OpenMPI
```

This naming convention is required so that the tool can infer:
- total core count
- MPI ranks
- OpenMP threads

---

#### 2. Filter out failed runs (strict by design)

A run is considered **valid only if** it contains a usable CP2K energy file:

```text
NVT1-1.ener
```

Rules:
- The file must exist
- It must contain at least one timestep **after step 0**
- Runs that do not meet these conditions are **excluded entirely**

Excluded runs are recorded in:

```text
report/skipped_missing_ener.txt
```

This ensures that all reported metrics correspond to *completed and meaningful* CP2K calculations.

---

#### 3. Extract CP2K timing statistics

From `NVT1-1.ener` the report computes:

- **Average `UsedTime[s]`**, excluding step 0
- **Standard deviation** of `UsedTime[s]`

These values are used as the primary performance metric throughout the report.

---

#### 4. Extract SLURM accounting data (optional)

If available, SLURM accounting data is queried using:

```bash
sacct --json
```

The job ID is inferred from `slurm_*.out` files in each benchmark directory.

Extracted quantities:

- Elapsed wall time (s)
- Total CPU time (s)
- Maximum resident set size (RSS, GB)

You can disable SLURM queries if `sacct` is unavailable:

```bash
cp2k-benchmarking qmmm report --no-sacct
```

---

#### 5. Compute derived performance metrics

##### CPU efficiency (%)

```text
CPU efficiency = CPU_time / (elapsed_time × total_cores) × 100
```

This value is clamped to the range **0–100%** in plots.

##### Speedup

```text
Speedup(p) = T₁ / Tₚ
```

Where:
- `T₁` is the **fastest valid 1‑core run** (minimum average `UsedTime[s]`)
- `Tₚ` is the average `UsedTime[s]` at `p` total cores

---

### Output files

All outputs are written to the directory specified by `--out` (default: `report/`).

#### Machine‑readable summary

```text
report/results.csv
```

---

### Interactive plots (HTML)

All plots are written as **self‑contained HTML files**. No server is required.

#### Big 2×2 summary plot (best‑per‑cores)

```text
report/summary_2x2_best_per_total_cores.html
```

This figure shows **only the fastest configuration for each total core count**.

Subplots:
- CPU efficiency (%)
- Maximum RSS (GB)
- Average UsedTime (s)
- Speedup (T₁ / Tₚ)

Features:
- Error bars (±1σ) toggle (OFF by default)
- Ideal reference lines: `T₁ / cores` and `y = x`

---

## Typical workflow

```bash
cp2k-benchmarking qmmm setup ...
cp2k-benchmarking submit
cp2k-benchmarking qmmm report
```


---

## License

MIT License

---

## Acknowledgements

Inspired by practical HPC benchmarking workflows and the structure of:

- https://github.com/geoffreyweal/orca-benchmarking
