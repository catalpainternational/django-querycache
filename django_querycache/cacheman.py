"""
Fingerprinting and serializer caching for Django models

    Typical usage example:

    Fingerprinting(Model.objects.all())
    CachedQuerySet(Model.objects.all())
"""

import datetime
import functools
import logging
import time
from functools import reduce
from hashlib import md5
from typing import Any, Dict, Iterable, List, Optional, Tuple, Type, Union

from django.apps import apps
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.core.cache.backends.dummy import DummyCache
from django.db import models
from django.db.models import F
from django.db.models.base import Model
from django.db.models.expressions import Func
from django.db.models.query import QuerySet

from .type_annotations import Feature

logger = logging.getLogger(__name__)

hstring = str  # Declare where we expect a hex encoded string of length 8
InputModel = Union[Model, QuerySet, Tuple[str, str]]  # Input models to these functions can take a number of forms


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


def timefunc(func):
    """Decorator function to log time taken by a function (in ms)"""

    @functools.wraps(func)
    def time_closure(*args, **kwargs):
        """Wrapped function will log the ms the function took"""
        start = time.perf_counter()
        result = func(*args, **kwargs)
        time_elapsed = time.perf_counter() - start
        logger.info(f"Function: {func.__name__}, Time: {(time_elapsed * 1000):.1f} ms")
        return result

    return time_closure


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


class RowHash(Func):
    """
    Trick to return the md5 hash of a whole postgres row
    """

    function = "MD5"
    template = 'substring(%(function)s("%(table)s"::text) from 0 for 8)'
    output_field = models.TextField()  # type: models.Field[Any, Any]


class SomeColsHash(RowHash):
    """
    Trick to return the md5sum of only some columns
    """

    template = "substring(%(function)s(%(expressions)s) from 0 for 8)"

    def as_sql(self, compiler, connection, function=None, template=None, arg_joiner="||", **extra_context):
        """
        Override the superclass to always cast fields to text
        """
        connection.ops.check_expression_support(self)
        sql_parts = []
        params = []
        for arg in self.source_expressions:
            arg_sql, arg_params = compiler.compile(arg)
            sql_parts.append(f"{arg_sql}::text")  # <-- Always cast to text for md5 sum of field
            params.extend(arg_params)
        data = {**self.extra, **extra_context}
        if function is not None:
            data["function"] = function
        else:
            data.setdefault("function", self.function)
        template = template or data.get("template", self.template)
        arg_joiner = arg_joiner or data.get("arg_joiner", self.arg_joiner)
        data["expressions"] = data["field"] = arg_joiner.join(sql_parts)
        return template % data, params


class Fingerprinting:
    """
    This class calculates and stores the fingerpring (hash) of a Django queryset.
    A fingerprint hash will be considered valid for `fingerprint_expiry` seconds.

    Parameters
    ----------
    query
        The model or query to hash
    cache_key
        The key to use for the fingerprint cache
    hashfields
        Fields to use for creating a row fingerprint (defaults to all)
    cache
        An instance with `get`, `set` and `delete` properties
    fingerprint_expiry
        Seconds to wait until assuming that the fingerprint needs recalculation
    time_cache_key
        The key to use for the fingerprint's "last calculated" time
    """

    def __init__(
        self,
        query: InputModel,
        cache_key: Optional[str] = None,
        hashfields: Optional[Iterable[Union[str, F]]] = (),
        fingerprint_expiry: int = 30,
        time_cache_key: Optional[str] = None,
        **kwargs,
    ):
        # Permit either a whole Model or a given Query to be used
        query, model = inputmodel_parse(query)
        self.query = query
        self.model = model

        self.cache = get_query_cache(kwargs.get("cache_alias", "default"))
        self._cache_is_dummy = isinstance(caches["default"], DummyCache)
        self.cache_key = cache_key or query_to_key(self.query, "_hash")
        self.time_cache_key = time_cache_key or f"{self.cache_key}_set_time"
        self.fingerprint_expiry = fingerprint_expiry or 30

        # Depending on whether certain rows are to be used or not
        # the hash function will be an md5 of whole table
        # or only some columns
        if hashfields:
            self.fingerprint: RowHash = SomeColsHash(*hashfields)
        else:
            self.fingerprint = RowHash(table=self.model._meta.db_table)

    @property
    def _cached_fingerprint(self):
        """
        Return the cached hash of the query's fingerprinting result
        """
        return self.cache.get(self.cache_key)

    @_cached_fingerprint.setter
    def _cached_fingerprint(self, value):
        """
        Sets the cached key and also the "sentinel" value of the last time the
        key was changed for time based validity checks
        """
        self.cache.set(self.time_cache_key, utcnow().timestamp())
        self.cache.set(self.cache_key, value)

    @_cached_fingerprint.deleter
    def _cached_fingerprint(self):
        self.cache.delete(self.time_cache_key)
        self.cache.delete(self.cache_key)

    @property
    def _expired(self) -> bool:
        """
        Time based expiration of the fingerprint result
        prevents spamming the fingerprint function if it was recently
        called
        """
        if not self._cached_fingerprint:
            logger.debug("Hash is not in the cache")
            return True
        cached_fingerprint_timestamp = self.cache.get(self.time_cache_key)
        if not cached_fingerprint_timestamp:
            logger.debug("Unknown hash date")
            return True
        age = datetime.datetime.now().timestamp() - cached_fingerprint_timestamp
        if age < self.fingerprint_expiry:
            logger.debug(
                "Fresh fingerprint: %s seconds expiring at %s seconds", round(age, 1), self.fingerprint_expiry
            )
            return False
        logger.debug("Old fingerprint: %s seconds", round(age, 1))
        return True

    def query_fingerprint(self) -> str:
        """
        Returns a single 8 character hex encoded string
        representing the database content of this query
        at this time
        """

        def row_fingerprints() -> List[str]:
            """
            Returns a list of named tuples with
            'fingerprints' for values returned by this queryset
            """
            return list(self.query.annotate(fingerprint=self.fingerprint).values_list("fingerprint", flat=True))

        def hexxor(a: hstring, b: hstring) -> hstring:
            """
            a and b are len 8 hex strings
            Note that 'x' = lowercase hex format
            """
            return ("%X" % (int(a, 16) ^ int(b, 16))).zfill(8)

        return reduce(hexxor, row_fingerprints(), "00000000")

    @timefunc
    def update_required(self, force_check=False) -> bool:
        """
        Return whether the cached query is considered "dirty" and
        a message informing reason for decision

        Parameters
        ----------
        force_check
            Ignore the age of the fingerprint and check it if not expired

        Returns
        -------
        bool
            True if the fingerprint changed, False if not
            or if the fingerprint was not expired
        """

        if not self._expired and not force_check:
            return False

        current_fp = self._cached_fingerprint
        new_fp = self.query_fingerprint()
        if current_fp == new_fp:
            self._cached_fingerprint = new_fp  # Still update to set the time key
            logger.debug("Fingerprint has not changed")
            return False
        logger.debug("Refreshing fingerprint for %s from %s to %s", self.cache_key, current_fp, new_fp)
        self._cached_fingerprint = new_fp
        logger.debug("Fingerprint has changed")
        return True


class TimeStampedFingerprint(Fingerprinting):
    """
    Where a class or query with an "auto_now" field is present
    we can use that field instead of calculating the md5sum of all rows
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if "fingerprint_expiry" not in kwargs:
            # The default expiry of 30s is probably too much for
            # the very low cost of a single last-modified
            # field, here we reduce it to 100ms
            self.fingerprint_expiry = 0.1

        # Set different hash keys for Timestamped vs hash key queries
        # Mostly of interest in testing
        self.cache_key = f"{self.cache_key}_ts"
        self.time_cache_key = f"{self.time_cache_key}_ts"

        # Use the specified column value, if provided;
        # otherwise search for a column with an 'auto_now' field

        self.timestamp_column = None
        if "timestamp_column" in kwargs:
            self.timestamp_column = kwargs.pop("timestamp_column")
        else:
            for field in self.model._meta.fields:
                if hasattr(field, "auto_now") and field.auto_now is True:
                    self.timestamp_column = field.name
                    break
        if not self.timestamp_column:
            raise ValueError("No timestamp column")
        logger.debug("using %s as timestamp column", self.timestamp_column)

    def query_fingerprint(self):
        """
        Returns the last updated time of the table or query rather than the
        hash of all query rows
        """
        try:
            ordered_query = self.query.order_by(self.timestamp_column)
        except TypeError as E:
            logger.debug(f"Encountered exception: {E}")
            logger.debug("Fall back to last_modified query for the whole model")
            ordered_query = self.model.objects.order_by(self.timestamp_column)
        last_updated = ordered_query.last()
        if not last_updated:
            logger.debug("Enpty query")
            return utcnow()
        last_timestamp = getattr(last_updated, self.timestamp_column)  # type: Union[datetime.date, datetime.datetime]
        # Expect a `isoformat` on this field
        return last_timestamp.isoformat()


class ModelTimeStampedFingerprint(TimeStampedFingerprint):
    """
    This class filters the "has_changed" return to check the last updated
    time for the query's whole model before running the fingerprint query
    which may have a slower result
    In many cases this should return faster than timestamp query over a few rows
    as it avoids the filtering steps; in the worst case it adds one additional
    but very fast query so it should probably be used as the default where
    a model has a timestamped column unless you have a huge table and
    a simple query
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.table_cache_key = f"{self.cache_key}_table"
        self.table_time_cache_key = f"{self.time_cache_key}_table"

    def _get_table_fingerprint(self):
        ordered_query = self.query.order_by(self.timestamp_column)
        last_updated = ordered_query.last()
        if not last_updated:
            logger.debug("Empty query")
            return utcnow()
        last_timestamp = getattr(last_updated, self.timestamp_column)  # type: Union[datetime.date, datetime.datetime]
        # Expect a `isoformat` on this field
        stamp = last_timestamp.isoformat()
        logger.debug(stamp)
        return stamp

    @property
    def _cached_table_fingerprint(self):
        """
        Return the cached hash of the query's fingerprinting result
        """
        return self.cache.get(self.table_cache_key)

    @_cached_table_fingerprint.setter
    def _cached_table_fingerprint(self, value):
        """
        Sets the cached key and also the "sentinel" value of the last time the
        key was changed for time based validity checks
        """
        self.cache.set(self.table_time_cache_key, utcnow().timestamp())
        self.cache.set(self.table_cache_key, value)

    @_cached_table_fingerprint.deleter
    def _cached_table_fingerprint(self):
        self.cache.delete(self.table_time_cache_key)
        self.cache.delete(self.table_cache_key)

    def update_required(self, force_check=False) -> bool:
        """
        Shortcut if the table has not changed since last checked
        """
        table_fp = self._get_table_fingerprint()
        if table_fp == self._cached_table_fingerprint:
            self._cached_table_fingerprint = table_fp
            logger.debug("Table not updated")
            return False
        self._cached_table_fingerprint = table_fp
        logger.debug("Table may have changed. Now checking if query has changed")
        required = super().update_required(force_check=force_check)  # type: bool
        return required


class CachedQuerySet:
    """
    Cache the serialized results of a query, using a 'Fingerprinting'
    instance to detect changes before updating results in the cache

    Attributes
    ----------
    cache
        An instance of a Django cache with get, set, delete methods
    _cache_is_dummy
        True if the cache will not do anything (Django's dummy cache)
    query: Queryset
        ...
    model: Model
        Django model. Derived from query if query is suitable.
    cache_key: str
        The unique key to use for this queryset
    fp
        Composition - a Fingerprint model

    """

    def __init__(
        self,
        query: InputModel,
        fp: Optional[Fingerprinting] = None,
        cache_key: str = None,
        query_values: Iterable[Union[str, F]] = (),
        **kwargs,
    ):

        self.cache = get_query_cache(kwargs.get("cache_alias", "default"))
        self._cache_is_dummy = isinstance(caches["default"], DummyCache)

        self.query, self.model = inputmodel_parse(query)

        self.query_values = query_values

        if self.query_values:
            self.query = self.query.values(*query_values)

        if cache_key:
            self.cache_key = cache_key
        else:
            self.cache_key = query_to_key(self.query)

        if fp:
            self.fp = fp
        else:
            try:
                self.fp = TimeStampedFingerprint(
                    query=self.query,
                    fingerprint_expiry=kwargs.get("fingerprint_expiry", None),
                )
            except ValueError:
                self.fp = Fingerprinting(
                    query=self.query,
                    fingerprint_expiry=kwargs.get("fingerprint_expiry", None),
                )

    @property
    def cached_query(self):
        if self._cache_is_dummy:
            return self.get_serialized_query()
        return self.cache.get(self.cache_key)

    @cached_query.setter
    def cached_query(self, value):
        logger.debug("Refreshing cache for %s", self.cache_key)
        self.cache.set(self.cache_key, value)

    @timefunc
    def get_with_update(self) -> Any:
        """
        Return the cached query if fresh else
        update and then return the cached query

        Returns
        -------
        Any
            The serialized value from the cache
        """
        self.update_if_required()
        return self.cached_query

    def update_if_required(self):
        """
        Check whether the fingerprint is fresh, and if
        the fingerprint has expired and changed
        then update the cache for this class' query
        """
        if self.cache_key not in self.cache:
            self.update_cache()
            logger.info("No data in cache, updating")
            # This is called to refresh the fingerprint
            self.fp.update_required()
        elif self.fp.update_required():
            self.update_cache()
            logger.info("Fingerprinting showed database changes, updated")
        else:
            logger.info("Fingerprint was recent or unchanged")

    @timefunc
    def update_cache(self):
        if self._cache_is_dummy:
            return
        self.cached_query = self.get_serialized_query()

    def get_serialized_query(self) -> List[Dict]:
        """
        Convert the results of 'get_query' into a serializable instance
        """
        if self.query_values:
            return list(self.query.values(*self.query_values))
        return list(self.query.values())


class GeoJsonCachedQuerySet(CachedQuerySet):
    """
    Adds additional properties and methods to serialize
    GeoJSON features

    This intended to work alongside a class with `get_query_for_cache()`
    and `get_with_update()` methods
    """

    def __init__(
        self, model: InputModel, geojson_props: Iterable[str] = [], geometry_field: str = "feature", *args, **kwargs
    ):
        """
        Parameters
        ----------
        geojson_props
            An iterable of strings, fields on the query, to turn into geojson "properties"
        geometry_field
            The name of a field to use, it should be a GeoJSON annotation not a geometry field
        """
        self.geojson_props = geojson_props
        self.geometry_field = geometry_field
        super().__init__(model, *args, **kwargs)

    def feature_props(self, item: Type[Model]) -> dict:
        """
        You might wish to override the generation of
        geojson feature properties here
        """
        return {field: getattr(item, field) for field in self.geojson_props}

    @timefunc
    def get_serialized_query(self) -> List[Feature]:
        """
        Django has a built in geometry serializer
        It does not work here because it requires geom to be
        an actual field not an annotation

        Returns
        -------
        List[Feature]
            Serialized features in GeoJSON format
        """
        return [
            {
                "type": "Feature",
                "geometry": getattr(item, self.geometry_field),
                "properties": self.feature_props(item),
            }
            for item in self.query
        ]

    @timefunc
    def features(self) -> List[Feature]:
        """
        This will update the features in the cache if necessary and return them
        It's an alias of 'get_with_update'

        Returns
        -------
        List[Feature]
            Serialized features in GeoJSON format
        """
        features = super().get_with_update()  # type: List[Feature]
        return features
