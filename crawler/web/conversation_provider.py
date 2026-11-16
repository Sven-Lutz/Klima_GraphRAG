"""Explicit qualified local Ollama configuration; no downloads or remote endpoints."""

import hashlib
import http.client
import json
import re
from pathlib import Path

from crawler.core.answer_generation import OllamaGenerationProvider, ProviderUnavailableError


class QualifiedLocalProvider:
    def __init__(self, model, digest):
        self.model, self.digest = model, digest
        self.delegate = OllamaGenerationProvider("http://127.0.0.1:11434")

    def generate(self, request):
        # An existing tag is necessary: Ollama must not select or pull an unbound model.
        conn = http.client.HTTPConnection("127.0.0.1", 11434, timeout=2)
        try:
            conn.request("GET", "/api/tags")
            response = conn.getresponse()
            raw = response.read(65537)
            if response.status != 200 or len(raw) > 65536:
                raise ValueError("Invalid bounded model inventory")
            models = json.loads(raw)["models"]
            if not any(m["name"] == self.model and m["digest"] == self.digest for m in models):
                raise ValueError("Qualified digest not available")
        except Exception as exc:
            raise ProviderUnavailableError("Qualified local model unavailable") from exc
        finally:
            conn.close()
        return self.delegate.generate(request)


def load_generation(path):
    """Owner supplies qualification evidence; the loader does not manufacture approval."""
    path = Path(path).resolve(strict=True)
    if path.stat().st_size > 16000:
        raise ValueError("Configuration budget exceeded")
    config = json.loads(path.read_text())
    required = {
        "provider",
        "model",
        "digest",
        "qualification",
        "qualification_artifact",
        "qualification_sha256",
        "license_reviewed",
        "weights_bytes",
        "execution",
    }
    if not required <= config.keys():
        raise ValueError("Incomplete qualified model configuration")
    if (
        config["provider"] != "ollama"
        or config["execution"] != "local"
        or config["qualification"] != "EXPLICITLY_QUALIFIED"
        or config["license_reviewed"] is not True
        or type(config["weights_bytes"]) is not int
        or not 0 < config["weights_bytes"] <= 1_500_000_000
    ):
        raise ValueError("Local qualification, license and bounded weights required")
    if not isinstance(config["model"], str) or not config["model"].strip():
        raise ValueError("Explicit model tag required")
    if not isinstance(config["digest"], str) or not re.fullmatch(
        r"(?:sha256:)?[0-9a-f]{64}", config["digest"]
    ):
        raise ValueError("Explicit model digest required")
    artifact = (path.parent / config["qualification_artifact"]).resolve(strict=True)
    state = artifact.stat()
    if (
        not artifact.is_file()
        or state.st_size > 1024**2
        or getattr(state, "st_flags", 0) & 0x40000000
    ):
        raise ValueError("Bounded resident qualification artifact required")
    with artifact.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != config["qualification_sha256"]:
            raise ValueError("Qualification artifact hash mismatch")
    metadata = dict(
        config,
        role="answer",
        availability="CHECKED_BEFORE_EACH_GENERATION",
        privacy="LOCAL_ONLY",
        cost_status="LOCAL_COMPUTE_NO_PAID_API",
        context_window=4096,
        hardware="8 GB device; one model worker; <=1.5GB weights",
        version=config["digest"],
        parameters={"temperature": 0, "max_tokens": 512},
    )
    metadata.pop("qualification_artifact")
    metadata["qualification_artifact"] = config["qualification_sha256"]
    return {
        "metadata": metadata,
        "provider": QualifiedLocalProvider(config["model"], config["digest"]),
    }
