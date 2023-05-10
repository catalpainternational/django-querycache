# django-querycache

[![codecov](https://codecov.io/gh/catalpainternational/django-querycache/branch/main/graph/badge.svg?token=N3wVm3FVoc)](https://codecov.io/gh/catalpainternational/django-querycache)

Cache manager for Django querysets / serialization

This combines Django's cache mechanism, a queryset / model and a serialization format, in order that you don't need to query or serialize data which is (according to either timestamp or MD5 sum of the rows in your query) unchanged

## Fingerprinting

This module takes a Django model, queryset or (app_name, model_name) tuple and generates an 8 character md5 hash of the contents. It caches that hash along with the time the hash was generated using a unique key for each query.

## TimeStampedFingerprint

Where a model uses a field with `auto_now` this field can be used as an alternative to generating the md5 hash, saving a bit of processor thrashing and i/o. You might want to check that you're using a `DateTimeField` rather than a `DateField` however or your cache might be unexpectedly stale.

## CachedQuerySet

Takes a Django model, queryset or (app_name, model_name) tuple and optional params. If a `Fingerprinting` instance is provided it's used to generate hashes and detect changes; otherwise a Fingerprint instance is generated on init. This allows different cache backends and even different queries to be used for the fingerprint and cache, which is beneficial when there are costly database calls (one example being geographic processing... mvt tiles perhaps?)

## GeoJsonCachedQuerySet

This is a special form of CachedQuerySet which should generate valid GeoJSON features from a model or queryset. Note that the geometry field is expected to be a GeoJSON field not a geometry field.

## Development

This project uses poetry for deploment

Optional (but recommended): Install `pre-commit` with

```bash
pip install pre-commit
pre-commit install
```

## Publishing

- Bump the `pyproject.yaml` version number
- Run `poetry build`
- Run `poetry publish`

### Auth

You may need your API token from pypi, or generate one [from pypi](https://pypi.org/manage/account/token/). The api token will start with 'pypi'.

`poetry publish --build --username __token__  --password pypi-AgE.........Q`

You may need to be added as a contributor on pypi - if so please ask @joshbrooks to add you

## Testing

From the root directory run tests with

```bash
poetry run runtests.py
```

This uses the test setting in `tests`. You may wish to set an env variable for a different settings if you want to use a different setup (note tests use the postgis container specified in the github actions)

To run the container as specified in the actions, run a postgres container on 49158 with password `post1233`

```sh
docker run \
    --rm \
    -p 49158:5432 \
    -e POSTGRES_PASSWORD="post1233" \
    postgis/postgis:14-3.2\
    -c fsync=off
```

### Github Actions

Github actions run a test suite for code clarity (black, isort, flake8, mypy) as well as the django tests above
