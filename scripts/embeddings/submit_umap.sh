#!/bin/bash
#SBATCH -p compute
#SBATCH --exclusive
#SBATCH -N 1
#SBATCH -n 1
#SBATCH -c 60
#SBATCH --time=8:00:00
# One whole node per UMAP configuration (issue #241). This is *not* the
# cluster `submit.sh` was written for: that one is `cemieredes`, with GPUs and
# its own `/home/mgraffg/.venvs/cluster` venv. Here the nodes have no GPU at
# all (`GRES=(null)`), `/home` is shared, and the repository's own synced
# `.venv` is visible from every node -- so it is the one interpreter the login
# node and all three jobs can agree on.
#
# Hardcoded rather than resolved from `${BASH_SOURCE[0]}`, for the reason
# `submit.sh` already records: Slurm copies this script into its own spool
# directory before running it, so that path no longer points at the repo.
PYTHON="/home/mgraffg/software/LegalIA/.venv/bin/python"

# No retry loop, unlike submit.sh: a UMAP fit that dies has burned an hour of
# a whole node, and issue #241's failure policy is to report it (with the tail
# of this job's own output) rather than silently start it again.
#
# Usage: sbatch --exclude=geoint0 --output=<dir>/slurm-%j.out submit_umap.sh \
#            project_umap.py --work-dir emb-run-umap --n-neighbors 15 --knn 15
exec "$PYTHON" "$@"
