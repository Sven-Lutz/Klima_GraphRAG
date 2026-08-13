#!/bin/bash
#SBATCH --job-name=klima_neo4j_export
#SBATCH --output=logs/neo4j_export_%j.out
#SBATCH --error=logs/neo4j_export_%j.err
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=00:05:00
#SBATCH --partition=cpu

set -euo pipefail

echo "Stage 4 is disabled: the HPC workflow is not yet migrated to the canonical"
echo "snapshot/run-scoped Neo4j projection. See docs/NEO4J_PROJECTION.md."
exit 2
