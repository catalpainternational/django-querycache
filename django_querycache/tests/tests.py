import logging

# Create your tests here.
from time import sleep

from django.core.cache import cache
from django.db.models import Func, JSONField
from django.test import TestCase
from tests.models import ModelOfRandomness, ModelOfRandomnessWithLastUpdated

from django_querycache.cacheman import CachedQuerySet, GeoJsonCachedQuerySet
from django_querycache.fingerprinting import Fingerprinting, ModelTimeStampedFingerprint
from django_querycache.hashfunctions import RowHash, SomeColsHash

logger = logging.getLogger(__name__)


class SomeTestCase(TestCase):
    def setUp(self):
        for n in range(5):
            ModelOfRandomness().save()

    def test_fingerprint(self):

        # A fingerprint can be generated from a model
        fp_from_model = Fingerprinting(ModelOfRandomness)

        # Or a queryset
        fp_from_query = Fingerprinting(ModelOfRandomness.objects.all())

        # Or an app/model string
        fp_from_names = Fingerprinting(("tests", "ModelOfRandomness"))

        # All should return the same hash

        self.assertEqual(fp_from_model.query_fingerprint(), fp_from_names.query_fingerprint())
        self.assertEqual(fp_from_model.query_fingerprint(), fp_from_query.query_fingerprint())

    def test_fingerprint_order(self):
        """A fingerprint of a query in any order should return the same value"""
        self.assertEqual(
            Fingerprinting(ModelOfRandomness.objects.all()).query_fingerprint(),
            Fingerprinting(ModelOfRandomness.objects.all().reverse()).query_fingerprint(),
        )

    def test_fingerprint_order_slice(self):
        """A fingerprint of different queries should return diff values"""
        self.assertNotEqual(
            Fingerprinting(ModelOfRandomness.objects.all()).query_fingerprint(),
            Fingerprinting(ModelOfRandomness.objects.all()[:4]),
        )

    def test_hashfields(self):
        """A fingerprinting class can specify the fields to use for the hash function"""

        fp_from_model = Fingerprinting(ModelOfRandomness)
        assert isinstance(fp_from_model.fingerprint, RowHash)

        fp_from_model = Fingerprinting(ModelOfRandomness, hashfields=("some_text",))
        assert isinstance(fp_from_model.fingerprint, SomeColsHash)

    def test_fingerprint_update(self):
        """A fingerprint should return False for `update_required`"""
        fp = Fingerprinting(ModelOfRandomness)
        self.assertTrue(fp.update_required())

        # Within 30 seconds
        sleep(0.01)
        self.assertFalse(fp.update_required())

        # Pretend that the fingerprint has changed
        fp._cached_fingerprint = "an old key"
        fp.fingerprint_expiry = 0
        sleep(0.01)
        self.assertTrue(fp.update_required())

        # Now with no more changes
        sleep(0.01)
        self.assertFalse(fp.update_required())

        # fingerprint.update_required updates and returns a True value if the fingerprint is not present yet
        del fp._cached_fingerprint
        self.assertTrue(fp.update_required())


class TimedTestCase(TestCase):

    """
    Test that a column with a "auto_now" field
    is used when appropriate as the hash field
    """

    def setUp(self):
        for n in range(5):
            ModelOfRandomnessWithLastUpdated().save()

    def test_timestampedfingerprint_raises(self):
        """
        Calling on a model w/o a timestamp field should raise a ValueError
        """
        with self.assertRaises(ValueError):
            ModelTimeStampedFingerprint(ModelOfRandomness)

    def test_timestampedfingerprint(self):
        fp = ModelTimeStampedFingerprint(ModelOfRandomnessWithLastUpdated, fingerprint_expiry=-1)

        self.assertTrue(fp.update_required())
        self.assertFalse(fp.update_required())

        # After saving one object in the query
        # the fingerprint has changed to the last timestamp
        m = ModelOfRandomnessWithLastUpdated.objects.last()
        m.save()
        self.assertTrue(fp.update_required())

    def test_fingerprint_changes_on_delete(self):
        """
        Deleted fields should cause "Change Required"
        """
        fp = ModelTimeStampedFingerprint(ModelOfRandomnessWithLastUpdated, fingerprint_expiry=-1)
        self.assertTrue(fp.update_required())
        self.assertFalse(fp.update_required())

        # Going to delete the oldest fp
        ModelOfRandomnessWithLastUpdated.objects.order_by("last_updated").first().delete()
        sleep(0.01)

        # This should now say that updates are required
        self.assertTrue(fp.update_required())


class CachedQuerySetTestCase(TestCase):
    def setUp(self):
        cache.clear()
        for n in range(5):
            ModelOfRandomness().save()
            ModelOfRandomnessWithLastUpdated().save()

    def test_cached_queryset_model(self):
        "Models"
        CachedQuerySet(ModelOfRandomness).get_with_update()
        CachedQuerySet(ModelOfRandomnessWithLastUpdated).get_with_update()

    def test_cached_queryset(self):
        "Querysets"
        CachedQuerySet(ModelOfRandomness.objects.all()).get_with_update()
        CachedQuerySet(ModelOfRandomnessWithLastUpdated.objects.all()).get_with_update()

    def test_sliced_queryset(self):
        """
        When a slice is taken it's not possible to do `last_updated` for the query
        instead last_updated for the whole model is taken
        """
        CachedQuerySet(ModelOfRandomness.objects.all()[:4]).get_with_update()
        CachedQuerySet(ModelOfRandomnessWithLastUpdated.objects.all()[:4]).get_with_update()

    def test_geo_cached_queryset(self):
        """
        This ought to create valid JSON
        TODO: Add GeoJSON validator
        """
        GeoJsonCachedQuerySet(
            ModelOfRandomnessWithLastUpdated.objects.annotate(
                feature=Func("point_field", function="ST_ASGEOJSON", output_field=JSONField())
            ),
            geojson_props=("pk", "category"),
        ).get_with_update()


class TestFullHashTestCase(TestCase):
    """
    Test that we can fetch the "full hash" of a table if desired for greater accuracy
    """

    def setUp(self):
        for n in range(5):
            ModelOfRandomness().save()

    def test_long_fingerprint(self):
        """
        A lng fingerprint should be 32 chars
        A short fingerprint should be 8 chars
        The output of a short fingerprint should
        be the same as the first 8 chars of the output
        of a long fingerprint
        """
        fp_from_model = Fingerprinting(ModelOfRandomness)
        fp_from_model_long = Fingerprinting(ModelOfRandomness, long_hash=True)

        self.assertEqual(fp_from_model_long.query_fingerprint()[:8], fp_from_model.query_fingerprint())
        self.assertEqual(len(fp_from_model.query_fingerprint()), 8)
        self.assertEqual(len(fp_from_model_long.query_fingerprint()), 32)
