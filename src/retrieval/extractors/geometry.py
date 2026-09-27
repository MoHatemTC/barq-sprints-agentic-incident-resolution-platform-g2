"""Bbox geometry shared by the table and layout extractors.

One implementation on purpose. The table extractor uses containment to decide
what is nested inside what; the layout extractor uses it to keep a checkbox off
the value beside it. Same question, same arithmetic, two copies of the same six
lines is one copy too many -- and they had already started to drift.
"""

from __future__ import annotations

from typing import Sequence


def containment(inner: Sequence[float], outer: Sequence[float]) -> float:
    """Fraction of ``inner``'s area that falls inside ``outer``, 0.0 to 1.0.

    Both rects are ``(x0, y0, x1, y1)`` in PDF points, the space ``layout.py``
    reports region bboxes in and ``tables.py`` reports cell bboxes in. A
    degenerate ``inner`` -- zero or negative width or height -- has no area to be
    contained, so it scores 0.0 rather than dividing by zero.

    This is area, not intersection-over-union: a rect entirely inside ``outer``
    scores 1.0 whether or not it fills it, which is what both callers mean by
    "is this inside that".
    """
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    if area <= 0:
        return 0.0
    x = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    y = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    return (x * y) / area
