"""Synthetic gateway contract only; not an external invitation/OTP acceptance."""

import pytest
from test_pilot_release import boundary, credentials


@pytest.mark.parametrize(
    "path",
    [
        "/review",
        "/api/review-protocol/status",
        "/api/review-protocol/accounts",
        "/api/review-protocol/export",
        "/api/research/history",
        "/api/evaluation",
        "/docs",
        "/openapi.json",
        "/generated/private-live-pilot/access.json",
    ],
)
def test_private_surface_is_denied_even_to_authenticated_pilot(path):
    client = boundary()
    assert client.get(path).status_code == 401
    response = client.get(path, headers=credentials())
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found"}}
    assert response.headers["cache-control"] == "no-store"
