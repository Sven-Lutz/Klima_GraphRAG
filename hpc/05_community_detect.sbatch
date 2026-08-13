#!/bin/bash
#SBATCH --job-name=klima_community
#SBATCH --output=logs/community_%j.out
#SBATCH --error=logs/community_%j.err
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=00:05:00
#SBATCH --partition=cpu

set -euo pipefail

echo "Stage 5 is disabled: community detection still targets the removed legacy"
echo "Neo4j shape and has no canonical projection-bound lifecycle."
exit 2
