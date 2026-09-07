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

# Usage: sbatch submit.sh encode_shard.py --work-dir ~/emb-run \
#            --model Qwen/Qwen3-Embedding-0.6B --shard 0
max_attempts=3
attempt=1
until python "$@"; do
    status=$?
    if [ "$attempt" -ge "$max_attempts" ]; then
        exit "$status"
    fi
    echo "python $* failed (attempt $attempt/$max_attempts, exit $status); retrying..." >&2
    attempt=$((attempt + 1))
    sleep 30
done
