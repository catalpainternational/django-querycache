import datetime
import logging
from functools import reduce
from hashlib import md5
from typing import Iterable, List, Optional, Union

from django.core.cache import caches
from django.core.cache.backends.dummy import DummyCache
from django.db.models import F, Func
from django.db.models.aggregates import Count, Max

from .hashfunctions import RowFullHash, RowHash, SomeColsFullHash, SomeColsHash
from .type_annotations import InputModel, hstring
from .utils import get_query_cache, inputmodel_parse, query_to_key, utcnow

logger = logging.getLogger(__name__)


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
        long_hash: Optional[bool] = False,
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
        self.long_hash = long_hash

        # Depending on whether certain rows are to be used or not
        # the hash function will be an md5 of whole table
        # or only some columns. The md5 sum can optionally return a "full" hash
        # or a truncated 8 character hash (default)
        if hashfields:
            if long_hash:
                self.fingerprint: Func = SomeColsFullHash(*hashfields)
            else:
                self.fingerprint = SomeColsHash(*hashfields)

        else:
            if long_hash:
                self.fingerprint = RowFullHash(table=self.model._meta.db_table)
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
            return ("%X" % (int(a, 16) ^ int(b, 16))).zfill(len(a))

        return reduce(hexxor, row_fingerprints(), "0" * (32 if self.long_hash else 8))

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
        agg = self.query.aggregate(Count("pk"), newest_timestamp=Max(self.timestamp_column))
        hash_string = "{pk__count}{newest_timestamp}".format(**agg)
        return md5(hash_string.encode()).hexdigest()


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
        agg = self.model.objects.aggregate(Count("pk"), newest_timestamp=Max(self.timestamp_column))
        hash_string = "{pk__count}{newest_timestamp}".format(**agg)
        return md5(hash_string.encode()).hexdigest()

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
