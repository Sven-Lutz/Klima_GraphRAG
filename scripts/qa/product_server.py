"""Isolated full-corpus technical server; never creates human review decisions."""

import argparse
from pathlib import Path

import uvicorn

from crawler.web.local_product import create_local_product

p = argparse.ArgumentParser()
p.add_argument(
    "--manifest",
    type=Path,
    default=Path("research/bavaria/completion/release/federated-workspace.json"),
)
p.add_argument("--directory", type=Path, default=Path("generated/product-completion-technical"))
p.add_argument("--catalog", type=Path)
p.add_argument("--indexes", type=Path)
p.add_argument("--generation-config", type=Path)
p.add_argument("--port", type=int, default=8813)
a = p.parse_args()
a.directory.mkdir(parents=True, exist_ok=True)
# For baseline QA reuse the existing immutable index; LocalIndex validates its binding.
indexes = a.indexes
if (
    indexes is None
    and a.manifest.resolve()
    == Path("research/bavaria/completion/release/federated-workspace.json").resolve()
):
    indexes = Path("generated/local-research")
if indexes is not None:
    for name in ["terms-v1.sqlite", "semantic-v1"]:
        original = indexes / name
        link = a.directory / name
        if original.exists() and not link.exists():
            link.symlink_to(original.resolve(), target_is_directory=original.is_dir())
generation_config = None
if a.generation_config:
    from crawler.web.grounded_provider import GroundedModelConfig

    generation_config = GroundedModelConfig.read(a.generation_config)
app = create_local_product(
    a.manifest,
    a.directory,
    technical=True,
    evaluation_catalog=a.catalog,
    generation_config=generation_config,
)
# Reuse the existing term index only when its immutable binding has been checked by LocalIndex.
uvicorn.run(app, host="127.0.0.1", port=a.port, access_log=False)
