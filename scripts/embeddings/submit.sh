#!/bin/bash
#SBATCH --gres=gpu:1
#SBATCH -p compute
#SBATCH -c 1
#SBATCH -n 1
# One GPU, offline, with the retry loop `../Chimalli-overleaf/enviar.sh`
# already runs on this same cluster (issue #218 Fase 2): a transient CUDA
# driver error kills the job mid-shard, and encode_shard.py resumes rather
# than restarting, since a shard's own atomic write + .done marker is what
# makes re-running it free.
export HF_HUB_OFFLINE=1
# The compute nodes have no internet (verified from inside a job): every
# weight this needs was already fetched on headmaster by submit_jobs.py's
# `hf download` step, and HF_HUB_OFFLINE=1 makes a missing file fail loudly
# here instead of hanging on a request nothing will answer.
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

# `sbatch` inherits whatever `PATH` the submitting shell happened to have,
# which is exactly the fragility that let this job silently run on CPU
# (issue #218's own uv migration). One venv, shared with
# `../Chimalli-overleaf` (disk on this cluster is rationed -- see this
# directory's own README -- and per-project venvs each pull their own
# multi-GB torch/CUDA install), replacing the single conda "cuda" env both
# projects used before. Hardcoded rather than resolved from
# `${BASH_SOURCE[0]}`: Slurm copies this script into its own spool directory
# before running it, so that would no longer point at the repo anyway --
# this cluster's `/home` layout is already hardcoded elsewhere (this file's
# own usage comment, the README's `~/emb-run`).
PYTHON="/home/mgraffg/.venvs/cluster/bin/python"

# Usage: sbatch submit.sh encode_shard.py --work-dir ~/emb-run \
#            --model Qwen/Qwen3-Embedding-0.6B --shard 0
max_attempts=3
attempt=1
until "$PYTHON" "$@"; do
    status=$?
    if [ "$attempt" -ge "$max_attempts" ]; then
        exit "$status"
    fi
    echo "python $* failed (attempt $attempt/$max_attempts, exit $status); retrying..." >&2
    attempt=$((attempt + 1))
    sleep 30
done
