"""
Fingerprinting and serializer caching for Django models

    Typical usage example:

    Fingerprinting(Model.objects.all())
    CachedQuerySet(Model.objects.all())
"""

import logging
from typing import Any, Dict, Iterable, List, Optional, Type, Union

from django.core.cache import caches
from django.core.cache.backends.dummy import DummyCache
from django.db.models import F, Model

from .fingerprinting import Fingerprinting, TimeStampedFingerprint
from .type_annotations import Feature, InputModel
from .utils import get_query_cache, inputmodel_parse, query_to_key

logger = logging.getLogger(__name__)


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

    def get_serialized_query(self) -> List[Feature]:  # type: ignore
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
