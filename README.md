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

## Design Principles

- Filesystem is the source of truth
- No hidden state or databases
- Deterministic resource requests
- HPC-friendly, admin-transparent behavior
- Easy post-processing and reporting

---

## License

MIT License

---

## Acknowledgements

Inspired by practical HPC benchmarking workflows and the structure of:

- https://github.com/geoffreyweal/orca-benchmarking
