INSTALLED_APPS = ["django_querycache", "tests"]
# docker run -p 49158:5432 --restart=always  \
# -e POSTGRES_PASSWORD=post1233  --name=querycache-tests postgis/postgis:12-3.1

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "USER": "postgres",
        "PASSWORD": "post1233",
        "HOST": "localhost",
        "PORT": "49158",
    }
}

SECRET_KEY = "test_key"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
        }
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "DEBUG",  # change to DEBUG to see all queries
            "propagate": True,
        },
        "django_querycache": {"handlers": ["console"], "level": "DEBUG", "propagate": True},
    },
}

USE_TZ = True
