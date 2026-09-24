"""Recipe source — Today's Cook. No network call; rotates by day of year."""

import datetime as dt
import random


def fetch(recipe_rotation):
    """Pick the day's recipe.

    The rotation is shuffled with the year as the seed, then indexed by day
    of year. A plain `day_of_year % len` walks the list in a fixed order and
    pins each dish to the same calendar date every year; re-seeding annually
    varies both without needing any stored state — the same day always
    resolves to the same recipe, so rebuilding mid-day is stable.
    """
    if not recipe_rotation:
        return None

    today = dt.date.today()
    order = list(recipe_rotation)
    random.Random(today.year).shuffle(order)
    return order[today.timetuple().tm_yday % len(order)]
