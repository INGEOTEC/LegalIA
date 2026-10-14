#!/bin/bash
#SBATCH -p compute
#SBATCH -N 1
#SBATCH -n 1
#SBATCH --time=2:00:00
# The CPU-job wrapper for a cluster whose one node also runs GPU jobs and other
# users' work (issue #294): `cemieredes`. `submit_umap.sh` is written for
# `geoint` and says `--exclusive -c 60`, which on a shared node waits for the
# *whole* machine -- every other user's job and our own GPU shards -- before it
# starts. This one asks for no exclusivity; the caller states the cores and the
# memory on the `sbatch` line (`instrument_matrix.py --shared-node --cpus N
# --mem M`), which Slurm applies over anything written here.
#
# The interpreter is the one `submit.sh` already runs the GPU jobs with, shared
# through `/home`; `$LEGALIA_PYTHON` overrides it. Hardcoded rather than
# resolved from `${BASH_SOURCE[0]}` for the reason `submit.sh` records: Slurm
# copies this script into its own spool directory before running it.
#
# `sbatch` exports the submitting shell's environment, so `$PYTHONPATH` or
# `$LEGALVEC_CACHE_DIR` set there reach the job (a BM25 sweep needs `bm25s`,
# which that venv does not carry).
#
# No retry loop, as in `submit_umap.sh`: a job that dies is reported.
PYTHON="${LEGALIA_PYTHON:-/home/mgraffg/.venvs/cluster/bin/python}"
exec "$PYTHON" "$@"
