# CP2K QM/MM Benchmarking

Infrastructure for benchmarking **CP2K QM/MM calculations** over a range of MPI task counts using **SLURM job arrays**.

Inspired by:
- https://github.com/geoffreyweal/orca-benchmarking

---

## Installation

```bash
pip install git+https://github.com/geoffreyweal/cp2k-benchmarking
```

---

## Usage

### Prepare input files

Create a directory called `CP2K_Files/` containing everything needed to run a single CP2K job:

```
CP2K_Files/
├── run_nvt.sh
├── input.inp
├── BASIS_SET
├── POTENTIAL
└── ...
```

`run_nvt.sh` must exist and will be executed inside each benchmark directory.

---

### SLURM include file

Create a file called:

```
cp2k_benchmarking_submit_include.txt
```

Containing site-specific SLURM directives, for example:

```bash
#SBATCH --account=myproject
#SBATCH --partition=compute
#SBATCH --time=08:00:00
```

---

### Setup benchmarking directories

```bash
cp2k-benchmark setup   --cores 1-8,10-16%2,20-32%4   --mem-per-cpu 2000MB
```

This expands to:

```
1,2,3,4,5,6,7,8,10,12,14,16,20,24,28,32
```

---

### Submit jobs

```bash
cd CP2K_Benchmarking
sbatch submit_array.sl
```

---

## License

MIT
