from typing import Any, Dict, List, Optional, Tuple, TypedDict, Union


class IndicatorRow(TypedDict):
    """
    Type hinting for the input to 'indicator_table' objects
    """

    value: Optional[Union[str, int, float]]
    name: Optional[str]
    group: Optional[str]


class IndicatorTable(TypedDict):
    groups: Optional[List[str]]
    rows: List[IndicatorRow]


# The formal specifications for these types
# can be found at
# https://datatracker.ietf.org/doc/html/rfc7946#section-3.2


Position = Tuple[float, float]
PositionWithElevation = Tuple[float, float, float]

CoordsSinglePart = Union[List[Position], List[PositionWithElevation]]
CoordsMultiPart = List[CoordsSinglePart]


class Geometry(TypedDict):
    type: str
    coordinates: Union[CoordsSinglePart, CoordsMultiPart]


class Feature(TypedDict):
    geometry: Geometry
    properties: Dict[str, Any]
    type: str
    # Note that a Feature may also allow arbitrary additional data
    # which can't be handled by mypy


class FeatureCollection(TypedDict):
    type: str
    features: List[Feature]
