#!/usr/bin/env python
import os
import sys

import django
from django.conf import settings
from django.test.utils import get_runner
from coverage import Coverage

if __name__ == "__main__":
    os.environ["DJANGO_SETTINGS_MODULE"] = "tests.test_settings"
    django.setup()
    TestRunner = get_runner(settings)
    test_runner = TestRunner()
    cov = Coverage()
    cov.erase()
    cov.start()
    failures = test_runner.run_tests(["tests"])
    cov.stop()
    cov.save()
    covered = cov.report()
    sys.exit(bool(failures))