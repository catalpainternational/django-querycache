import logging
from typing import Any

from django.db import models
from django.db.models.expressions import Func

logger = logging.getLogger(__name__)


class RowFullHash(Func):
    """
    Trick to return the md5 hash of a whole postgres row
    """

    function = "MD5"
    template = '%(function)s("%(table)s"::text)'
    output_field = models.TextField()  # type: models.Field[Any, Any]


class RowHash(RowFullHash):
    """
    Trick to return the md5 hash of a whole postgres row.
    This returns the md5 hash, truncated to 8 characters for easier handling.
    """

    template = 'substring(%(function)s("%(table)s"::text) for 8)'


class SomeColsFullHash(RowFullHash):
    """
    Trick to return the md5sum of only some columns
    """

    template = "substring(%(function)s(%(expressions)s) for 8)"

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


class SomeColsHash(SomeColsFullHash):
    """
    Trick to return the md5sum of only some columns. This returns the
    md5 hash, truncated to 8 characters for easier handling.
    """

    template = "substring(%(function)s(%(expressions)s) from 0 for 8)"
