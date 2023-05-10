import datetime
import logging
from hashlib import md5
from typing import Optional, Sequence, Tuple

from django.apps import apps
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.db.models import Field, Model, QuerySet

from .type_annotations import InputModel

logger = logging.getLogger(__name__)


def get_query_cache(cache_alias: str = "default"):
    """
    For purposes of caching, particularly in local dev,
    Django's default cache (memory) is not really helpful
    It gets destroyed regularly (on each deployment at least)

    Specify an alternative cache here

    Suggested local config for this:

        CACHES['cacheman'] = {
            'BACKEND': 'diskcache.DjangoCache',
            'LOCATION': '/tmp/django-query-cache',
            'TIMEOUT': 7 * 24 * 60 * 60,  # 1 week
            'SHARDS': 8,
            'DATABASE_TIMEOUT': 0.010,  # 10 milliseconds
            'OPTIONS': {
                'size_limit': 2 ** 32   # 4 gigabyte
            },
        }
    """
    try:
        return caches[cache_alias]
    except Exception as E:  # noqa: F401,F841
        logger.warn('No "%s" cache. Using defult cache' % (cache_alias,))
        return default_cache


def query_to_key(query: QuerySet, suffix: str = "") -> str:
    """
    Parameters
    ----------
    query
        A queryset which will be parsed (table, sql, + placeholders) to create a unique key
    suffix
        Alter the key with extra text
    """
    # and query to generate a key
    query_hex = md5(query.query.sql_with_params()[0].encode()).hexdigest()[:4]
    params_hex = md5("".join(map(str, query.query.sql_with_params()[1])).encode()).hexdigest()[:4]
    return f"{query.query.base_table}_{query_hex}{params_hex}{suffix}"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def inputmodel_parse(inputthing: InputModel) -> Tuple[QuerySet, Model]:
    """
    Parameters
    ----------
    inputmodel
        One of: model, queryset, tuple
    Returns
    -------
    Queryset and model from an input
    """
    # Permit either a whole Model or a given Query to be used
    if hasattr(inputthing, "objects"):
        return inputthing.objects.all(), inputthing  # type: ignore
    elif hasattr(inputthing, "model"):
        return inputthing, inputthing.model  # type: ignore
    elif isinstance(inputthing, tuple):
        _m = apps.get_model(inputthing[0], inputthing[1])  # type: ignore
        query = _m.objects.all()  # type: QuerySet
        return query, _m  # type: ignore
    raise TypeError(f"Could not determine the model or queryset from {inputthing}")


def get_ts_field(inputthing: InputModel) -> Optional[Field]:
    """
    Parameters
    ----------
    inputmodel
        One of: model, queryset, tuple
    Returns
    -------
    A Field instance where "auto_now" is true
    """
    _, model = inputmodel_parse(inputthing)
    fields: Sequence[Field] = model._meta.fields

    for field in fields:
        if getattr(field, "auto_now", False):
            return field
    logger.warn("No timestamp column in: %s" % ([f.name for f in fields]))
    return None


def last_modified_queryset(inputthing: InputModel) -> Optional[str]:
    """
    Returns a [Last-Modified](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Last-Modified) header
    content for the current queryset, if there is an `auto_now` field present on the model
    """
    queryset, _ = inputmodel_parse(inputthing)
    time_format = "%a, %d %b %Y %H:%M:%S"  # Should correspond to the "Last-Modified" header docs
    if ts_field := get_ts_field(inputthing):
        dt: Optional[datetime.datetime] = (
            queryset.order_by(f"-{ts_field.name}").values_list(ts_field.name, flat=True).first()
        )
        if dt:
            return dt.strftime(time_format)
    return None
