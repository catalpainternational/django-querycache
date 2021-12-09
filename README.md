
# django-querycache

Cache manager for Django querysets / serialization

## Fingerprinting

This module takes a Django model, queryset or (app_name, model_name) tuple and generates an 8 character md5 hash of the contents. It caches that hash along with the time the hash was generated using a unique key for each query.

## TimeStampedFingerprint

Where a model uses a field with `auto_now` this field can be used as an alternative to generating the md5 hash, saving a bit of processor thrashing and i/o. You might want to check that you're using a `DateTimeField` rather than a `DateField` however or your cache might be unexpectedly stale.

## CachedQuerySet

Takes a Django model, queryset or (app_name, model_name) tuple and optional params. If a `Fingerprinting` instance is provided it's used to generate hashes and detect changes; otherwise a Fingerprint instance is generated on init. This allows different cache backends and even different queries to be used for the fingerprint and cache, which is beneficial when there are costly database calls (one example being geographic processing... mvt tiles perhaps?)

## GeoJsonCachedQuerySet

This is a special form of CachedQuerySet which shoould generate valid GeoJSON features from a model or queryset. Note that the geometry field is expected to be a GeoJSON field not a geometry field.

## Development

pre-commit is nice

'''bash
pip install pre-commit
pre-commit install
'''
