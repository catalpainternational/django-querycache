from http import HTTPStatus
from typing import Tuple

from django.db.models import QuerySet

from .fingerprinting import Fingerprinting
from .utils import last_modified_queryset

try:
    from rest_framework.response import Response
except ModuleNotFoundError:
    raise ModuleNotFoundError("Django rest framework is required for this feature")


class AddHeadersMixin:
    def _add_headers(self, object=None) -> Tuple[Tuple[str, str]]:
        queryset = self.filter_queryset(self.get_queryset())
        if object:
            queryset = queryset.filter(pk=object.pk)

        last_modified = last_modified_queryset(queryset)
        etag = Fingerprinting(query=queryset, hashfields=self.serializer_class.Meta.fields).query_fingerprint()
        headers: Tuple[Tuple[str, str]] = (("Last-Modified", last_modified), ("ETag", etag))

        return headers


class ListHeadersMixin:
    def list(self, request, *args, **kwargs):
        queryset: QuerySet = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data, headers=self._add_headers())

        serializer = self.get_serializer(queryset, many=True)

        headers = dict(self._add_headers())
        # Return a 304: Unchanged if the ETag and Last-Modified match
        if "If-None-Match" in request.headers and request.headers.get("If-None-Match") == headers["ETag"]:
            return Response(status=HTTPStatus.NOT_MODIFIED)

        return Response(serializer.data, headers=headers)


class RetrieveHeadersMixin:
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)

        headers = dict(self._add_headers())
        # Return a 304: Unchanged if the ETag and Last-Modified match
        if "If-None-Match" in request.headers and request.headers.get("If-None-Match") == headers["ETag"]:
            return Response(status=HTTPStatus.NOT_MODIFIED)

        return Response(serializer.data, headers=headers)


class ModelViewSetHeaders(AddHeadersMixin, ListHeadersMixin, RetrieveHeadersMixin):
    """
    Class modifies the "list" and "retrieve" actions
    to generate an ETag and Last-Modified. If there 
    are no changes a 304 response is returned.

    To use this class, add it as the first superclass
    to a Viewset. For instance:
    
    >>> class SIPRequirementValuesViewSet(ModelViewSetHeaders, viewsets.ModelViewSet):
    
    """
    pass
