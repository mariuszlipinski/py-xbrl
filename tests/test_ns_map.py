"""Regression tests for bundled namespace mappings."""

from xbrl.ns_map import NS_MAP


def test_cef_2026_namespace_mapping() -> None:
    assert NS_MAP["http://xbrl.sec.gov/cef/2026"] == "https://xbrl.sec.gov/cef/2026/cef-2026.xsd"
