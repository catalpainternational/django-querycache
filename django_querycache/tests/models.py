import secrets
from datetime import datetime
from typing import Any

from django.contrib.gis.db.models import MultiPolygonField
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.db import models


def poly():
    return MultiPolygon(
        [
            Polygon.from_ewkt(
                "POLYGON ((145.488969 -6.438297, 145.472753 -6.35001, 145.48618 -6.244191, 145.667748 -5.975847, 145.830266 -5.975848, 145.816106 -6.194881, 145.74765 -6.306579, 145.606876 -6.378374, 145.488969 -6.438297))"  # noqa
            )
        ]
    )


def category():
    return secrets.choice(["A", "B", "C", "D", "E"])


class ModelOfRandomness(models.Model):
    some_text = models.TextField(default=secrets.token_urlsafe)  # type: str
    category = models.TextField(default=category)  # type: str
    point_field = MultiPolygonField(default=poly)  # type: Any


class ModelOfRandomnessWithLastUpdated(models.Model):
    some_text = models.TextField(default=secrets.token_urlsafe)  # type: str
    category = models.TextField(default=category)  # type: str
    point_field = MultiPolygonField(default=poly)  # type: Any
    last_updated = models.DateTimeField(auto_now=True)  # type: datetime
