from datetime import datetime
from typing import Any

from django.db import models

def poly(): ...
def category(): ...

class ModelOfRandomness(models.Model):
    some_text: str
    category: str
    point_field: Any

class ModelOfRandomnessWithLastUpdated(models.Model):
    some_text: str
    category: str
    point_field: Any
    last_updated: datetime
