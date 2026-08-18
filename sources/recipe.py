"""Recipe source — Today's Cook. No network call; rotates by day of year."""

import datetime as dt


def fetch(recipe_rotation):
    if not recipe_rotation:
        return None
    day_of_year = dt.date.today().timetuple().tm_yday
    return recipe_rotation[day_of_year % len(recipe_rotation)]
