"""Token contrast checks, not a blanket WCAG conformance assertion."""

import re
from pathlib import Path


def luminance(value):
    if len(value) == 4:
        value = "#" + "".join(c * 2 for c in value[1:])
    rgb = [int(value[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return sum(v * w for v, w in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))


def test_light_only_tokens_meet_selected_text_and_control_contrasts():
    text = (Path(__file__).resolve().parents[1] / "crawler/web/static/tokens.css").read_text()
    assert "data-theme=dark" not in text
    for section in [text.split("}")[0]]:
        colors = dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-f]{3,6})", section))
        for first, second, minimum in [
            ("ink", "bg", 4.5),
            ("muted", "bg", 4.5),
            ("accent", "paper", 4.5),
            ("accent-ink", "accent", 4.5),
            ("warn", "warn-bg", 4.5),
            ("control-line", "paper", 3),
            ("focus", "paper", 3),
        ]:
            a, b = sorted((luminance(colors[first]), luminance(colors[second])))
            assert (b + 0.05) / (a + 0.05) >= minimum, (first, second, (b + 0.05) / (a + 0.05))
