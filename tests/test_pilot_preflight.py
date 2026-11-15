"""Offline preflight rejects accidental deployment exposure; never connects to a host."""

import shutil
from pathlib import Path

import pytest
import yaml

from deployment.pilot.preflight import inspect

ROOT = Path(__file__).resolve().parents[1]


def test_existing_configuration_is_bounded_but_not_go_live_approved():
    report = inspect(ROOT)
    assert report["status"] == "PASS"
    assert report["approved_real_sources_in_register"] == 0
    assert report["host_tls_auth_isolation_restore_tests"] == "NOT_TESTED"


@pytest.mark.parametrize(
    "mutation", ["publish_app", "write_snapshot", "public_network", "remove_auth"]
)
def test_preflight_rejects_exposure_regressions(tmp_path, mutation):
    folder = tmp_path / "deployment/pilot"
    folder.mkdir(parents=True)
    for name in ("compose.yml", "Caddyfile", "Dockerfile"):
        shutil.copyfile(ROOT / "deployment/pilot" / name, folder / name)
    rights = tmp_path / "docs/pilot/evidence"
    rights.mkdir(parents=True)
    shutil.copyfile(
        ROOT / "docs/pilot/evidence/RIGHTS_REGISTER.json", rights / "RIGHTS_REGISTER.json"
    )
    path = folder / "compose.yml"
    data = yaml.safe_load(path.read_text())
    if mutation == "publish_app":
        data["services"]["app"]["ports"] = ["8080:8080"]
    elif mutation == "write_snapshot":
        data["services"]["app"]["volumes"][0]["read_only"] = False
    elif mutation == "public_network":
        data["networks"]["research"]["internal"] = False
    else:
        p = folder / "Caddyfile"
        p.write_text(p.read_text().replace("basic_auth", "disabled_auth"))
    path.write_text(yaml.safe_dump(data))
    assert inspect(tmp_path)["status"] == "FAILED"


def test_image_inputs_must_all_be_immutable_and_are_not_assumed_reviewed():
    from deployment.pilot.preflight import image_check

    digest = "registry.example/test@sha256:" + "a" * 64
    assert image_check((None, None, None)) == "NOT_TESTED"
    assert image_check((digest, digest, digest)) == "PASS"
    assert image_check((digest, "caddy:latest", digest)) == "FAILED"
    assert image_check((digest, None, digest)) == "FAILED"
    assert image_check((digest, digest, "python@sha256:" + "z" * 64)) == "FAILED"
