#!/bin/bash
# Convenience script for quick worker updates
# Usage: ./register_workers.sh minimax=worker-17 qwen3_vl_fp8=worker-16 parakeet=worker-7

cd "$(dirname "$0")"
python -m app.cli.model_config workers "$@"
