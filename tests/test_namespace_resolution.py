"""Unit tests for trusted taxonomy namespace resolution fallbacks."""

from __future__ import annotations

from pathlib import Path

import pytest

from xbrl import TaxonomyNotFound
from xbrl.cache import HttpCache
from xbrl.taxonomy import TaxonomyParser


def _write_xsd(path: Path, target_namespace: str, hrefs: tuple[str, ...] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    references = "".join(
        f'<link:roleRef roleURI="urn:role:{index}" xlink:type="simple" xlink:href="{href}"/>'
        for index, href in enumerate(hrefs)
    )
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:link="http://www.xbrl.org/2003/linkbase" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'targetNamespace="{target_namespace}">'
        f"{references}</xsd:schema>\n",
        encoding="utf-8",
    )


class RecordingHttpCache(HttpCache):
    def __init__(self, cache_dir: str, responses: dict[str, Path]) -> None:
        super().__init__(cache_dir)
        self.responses = responses
        self.requests: list[str] = []

    def cache_file(self, file_url: str) -> str:
        self.requests.append(file_url)
        try:
            return str(self.responses[file_url])
        except KeyError as exc:
            raise OSError(f"Unexpected URL: {file_url}") from exc

    def url_to_path(self, url: str) -> str:
        response = self.responses.get(url)
        return str(response) if response is not None else super().url_to_path(url)


def test_resolves_from_local_cache_and_normalizes_namespace(tmp_path: Path) -> None:
    namespace = "http://xbrl.sec.gov/unit-test-tax/2099"
    schema_path = tmp_path / "xbrl.sec.gov" / "unit-test-tax" / "2099" / "unit-test-tax-2099.xsd"
    _write_xsd(schema_path, namespace)

    parser = TaxonomyParser(HttpCache(str(tmp_path)))
    assert namespace not in parser.global_ns_map

    taxonomy = parser.try_taxonomy_from_namespace(namespace + "/")

    assert taxonomy.namespace == namespace
    assert parser.global_ns_map[namespace] == str(schema_path)
    assert namespace + "/" not in parser.global_ns_map


def test_cache_lookup_uses_schema_host_associated_with_namespace_host(tmp_path: Path) -> None:
    namespace = "http://fasb.org/unit-test-tax/2099"
    schema_path = tmp_path / "xbrl.fasb.org" / "unit-test-tax" / "2099" / "unit-test-tax-2099.xsd"
    _write_xsd(schema_path, namespace)

    parser = TaxonomyParser(HttpCache(str(tmp_path)))
    taxonomy = parser.try_taxonomy_from_namespace(namespace)

    assert taxonomy.namespace == namespace
    assert parser.global_ns_map[namespace] == str(schema_path)
    assert "xbrl.fasb.org" in parser._cache_host_ns_index


def test_untrusted_cache_host_is_not_scanned(tmp_path: Path) -> None:
    namespace = "http://example.invalid/unit-test-tax/2099"
    schema_path = tmp_path / "example.invalid" / "unit-test-tax" / "2099" / "unit-test-tax-2099.xsd"
    _write_xsd(schema_path, namespace)
    parser = TaxonomyParser(HttpCache(str(tmp_path)))

    assert "example.invalid" not in parser.trusted_hosts
    assert parser._find_cached_schema_path_for_namespace(namespace) is None
    assert "example.invalid" not in parser._cache_host_ns_index
    with pytest.raises(TaxonomyNotFound):
        parser.try_taxonomy_from_namespace(namespace)


def test_opt_in_resolves_deduplicated_relative_referenced_schema(tmp_path: Path) -> None:
    pre_url = "https://xbrl.sec.gov/unit-test/2099/unit-test_pre.xsd"
    core_url = "https://xbrl.sec.gov/unit-test/2099/unit-test.xsd"
    namespace = "http://xbrl.sec.gov/unit-test/2099"
    pre_path = tmp_path / "pre.xsd"
    core_path = tmp_path / "core.xsd"
    _write_xsd(pre_path, namespace + "-pre", ("unit-test.xsd#role", "unit-test.xsd#concept"))
    _write_xsd(core_path, namespace)
    cache = RecordingHttpCache(str(tmp_path / "cache"), {pre_url: pre_path, core_url: core_path})
    parser = TaxonomyParser(cache, resolve_referenced_schemas=True)

    parser.parse_taxonomy(pre_url)
    taxonomy = parser.try_taxonomy_from_namespace(namespace)

    assert taxonomy.namespace == namespace
    assert parser.global_ns_map[namespace] == core_url
    assert parser._referenced_schema_urls == [core_url]
    assert cache.requests.count(core_url) == 1


def test_referenced_schema_candidate_collection_is_bounded(tmp_path: Path) -> None:
    pre_url = "https://xbrl.sec.gov/unit-test/2099/unit-test_pre.xsd"
    pre_path = tmp_path / "pre.xsd"
    _write_xsd(pre_path, "urn:pre", ("first.xsd#concept", "second.xsd#concept"))
    cache = RecordingHttpCache(str(tmp_path / "cache"), {pre_url: pre_path})
    parser = TaxonomyParser(cache, resolve_referenced_schemas=True)
    parser._max_referenced_schema_urls = 1

    parser.parse_taxonomy(pre_url)

    assert parser._referenced_schema_urls == ["https://xbrl.sec.gov/unit-test/2099/first.xsd"]


def test_referenced_schema_requires_matching_target_namespace(tmp_path: Path) -> None:
    pre_url = "https://xbrl.sec.gov/unit-test/2099/unit-test_pre.xsd"
    candidate_url = "https://xbrl.sec.gov/unit-test/2099/candidate.xsd"
    wanted_namespace = "http://xbrl.sec.gov/unit-test/2099"
    pre_path = tmp_path / "pre.xsd"
    candidate_path = tmp_path / "candidate.xsd"
    _write_xsd(pre_path, wanted_namespace + "-pre", ("candidate.xsd#concept",))
    _write_xsd(candidate_path, "http://xbrl.sec.gov/different/2099")
    cache = RecordingHttpCache(str(tmp_path / "cache"), {pre_url: pre_path, candidate_url: candidate_path})
    parser = TaxonomyParser(cache, resolve_referenced_schemas=True)

    parser.parse_taxonomy(pre_url)

    with pytest.raises(TaxonomyNotFound):
        parser.try_taxonomy_from_namespace(wanted_namespace)
    assert wanted_namespace not in parser.global_ns_map
    assert cache.requests.count(candidate_url) == 1


def test_opt_in_collects_schema_references_from_external_linkbase(tmp_path: Path) -> None:
    extension_url = "https://xbrl.sec.gov/unit-test/2099/extension.xsd"
    linkbase_url = "https://xbrl.sec.gov/unit-test/2099/extension_pre.xml"
    core_url = "https://xbrl.sec.gov/unit-test/2099/unit-test.xsd"
    namespace = "http://xbrl.sec.gov/unit-test/2099"
    extension_path = tmp_path / "extension.xsd"
    linkbase_path = tmp_path / "extension_pre.xml"
    core_path = tmp_path / "core.xsd"
    extension_path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:link="http://www.xbrl.org/2003/linkbase" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" targetNamespace="urn:extension">'
        '<xsd:annotation><xsd:appinfo><link:linkbaseRef xlink:type="simple" '
        'xlink:role="http://www.xbrl.org/2003/role/presentationLinkbaseRef" '
        'xlink:href="extension_pre.xml"/></xsd:appinfo></xsd:annotation></xsd:schema>\n',
        encoding="utf-8",
    )
    linkbase_path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" '
        'xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<link:roleRef roleURI="urn:role:test" xlink:type="simple" '
        'xlink:href="unit-test.xsd#role"/></link:linkbase>\n',
        encoding="utf-8",
    )
    _write_xsd(core_path, namespace)
    cache = RecordingHttpCache(
        str(tmp_path / "cache"), {extension_url: extension_path, linkbase_url: linkbase_path, core_url: core_path}
    )
    parser = TaxonomyParser(cache, resolve_referenced_schemas=True)

    parser.parse_taxonomy(extension_url)
    taxonomy = parser.try_taxonomy_from_namespace(namespace)

    assert taxonomy.namespace == namespace
    assert core_url in parser._referenced_schema_urls


def test_untrusted_referenced_schema_is_never_fetched(tmp_path: Path) -> None:
    pre_url = "https://xbrl.sec.gov/unit-test/2099/unit-test_pre.xsd"
    untrusted_url = "https://example.invalid/unit-test.xsd"
    namespace = "http://xbrl.sec.gov/unit-test/2099"
    pre_path = tmp_path / "pre.xsd"
    _write_xsd(pre_path, namespace + "-pre", (untrusted_url + "#concept",))
    cache = RecordingHttpCache(str(tmp_path / "cache"), {pre_url: pre_path})
    parser = TaxonomyParser(cache, resolve_referenced_schemas=True)

    parser.parse_taxonomy(pre_url)

    with pytest.raises(TaxonomyNotFound):
        parser.try_taxonomy_from_namespace(namespace)
    assert untrusted_url not in cache.requests
    assert parser._referenced_schema_urls == []


def test_referenced_schema_network_resolution_is_disabled_by_default(tmp_path: Path) -> None:
    pre_url = "https://xbrl.sec.gov/unit-test/2099/unit-test_pre.xsd"
    core_url = "https://xbrl.sec.gov/unit-test/2099/unit-test.xsd"
    namespace = "http://xbrl.sec.gov/unit-test/2099"
    pre_path = tmp_path / "pre.xsd"
    core_path = tmp_path / "core.xsd"
    _write_xsd(pre_path, namespace + "-pre", ("unit-test.xsd#concept",))
    _write_xsd(core_path, namespace)
    cache = RecordingHttpCache(str(tmp_path / "cache"), {pre_url: pre_path, core_url: core_path})
    parser = TaxonomyParser(cache)

    parser.parse_taxonomy(pre_url)

    with pytest.raises(TaxonomyNotFound):
        parser.try_taxonomy_from_namespace(namespace)
    assert core_url not in cache.requests
