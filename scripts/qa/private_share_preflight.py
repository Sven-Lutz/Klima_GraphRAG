"""Read-only readiness check; never provisions a tunnel or releases source data."""

import argparse
import json
import shutil
from pathlib import Path


def inspect(config_path):
    path = Path(config_path)
    config = json.loads(path.read_text())
    emails = config.get("allowed_mail", [])
    privacy = config.get("provider_privacy_review", {})
    blockers = []
    if path.is_symlink() or path.stat().st_mode & 0o077:
        blockers.append("PRIVATE_CONFIG_PERMISSIONS")
    if len(emails) != 3 or len(set(emails)) != 3 or any("*" in e for e in emails):
        blockers.append("EXACT_THREE_PERSON_ALLOWLIST")
    if privacy.get("status") != "DOCUMENTED_FOR_THIS_SERVICE":
        blockers.append("APPLICABLE_PROVIDER_AGREEMENT_EVIDENCE_MISSING")
    elif not all(
        privacy.get(k)
        for k in (
            "applicable_agreement",
            "processing_roles_and_article28",
            "transfer_basis",
            "reviewed_by",
        )
    ):
        blockers.append("INCOMPLETE_SERVICE_SPECIFIC_PROVIDER_EVIDENCE")
    if config.get("source_presentation") == "minimal_external_preview":
        blockers.append("EXISTING_RIGHTS_COVER_FOUR_FACTS_NOT_FULL_BAVARIA_EVIDENCE")
    else:
        blockers.append("FULL_CORPUS_EXPOSURE_RECEIPT_NOT_VERIFIED")
    if shutil.disk_usage(path.parent).free < 15 * 1024**3:
        blockers.append("SSD_RESERVE")
    return dict(
        schema="private-share-readiness-v1",
        shareability="BLOCKED",
        blockers=blockers,
        configured_people=len(emails),
        actual_https_test="NOT_RUN",
        authentication_test="NOT_RUN",
        external_process_started=False,
        local_full_corpus_is_not_the_external_minimal_projection=True,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(inspect(a.config), indent=2))
