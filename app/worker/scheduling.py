"""Natural-ish delay sampling for repost tasks."""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from app.config import get_settings


def sample_repost_time(base: datetime) -> datetime:
    """Return a scheduled_at in the configured repost window.

    Median is one hour by default, clipped to [60s, 5h]. This produces a dense
    first half and a long tail without pretending to be "random human behavior".
    """
    settings = get_settings()
    mu = math.log(settings.repost_lognormal_median_seconds)
    delay = math.exp(random.gauss(mu, settings.repost_lognormal_sigma))
    delay = max(settings.repost_min_delay_seconds, delay)
    delay = min(settings.repost_window_seconds, delay)
    return base + timedelta(seconds=round(delay))
