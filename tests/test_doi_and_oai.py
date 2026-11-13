"""DOI 归一化与 OAI 响应解析的单元测试。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harvester.doi_utils import normalize_doi, looks_like_doi
from harvester.oai import parse_list_records, OaiError, build_list_records_url


class TestNormalize(unittest.TestCase):
    def test_prefixes_and_case(self):
        variants = [
            "https://doi.org/10.1001/ABC.1",
            "http://doi.org/10.1001/abc.1",
            "https://dx.doi.org/10.1001/ABC.1",
            "http://dx.doi.org/10.1001/abc.1",
            "doi:10.1001/ABC.1",
            "10.1001/ABC.1",
            "10.1001/abc.1",
        ]
        keys = {normalize_doi(v) for v in variants}
        self.assertEqual(keys, {"10.1001/abc.1"})

    def test_empty(self):
        self.assertIsNone(normalize_doi(None))
        self.assertIsNone(normalize_doi(""))
        self.assertIsNone(normalize_doi("   "))

    def test_looks_like_doi(self):
        self.assertTrue(looks_like_doi("https://doi.org/10.1/x"))
        self.assertTrue(looks_like_doi("doi:10.1/x"))
        self.assertTrue(looks_like_doi("10.1000/xyz123"))
        self.assertFalse(looks_like_doi("urn:isbn:123456"))
        self.assertFalse(looks_like_doi("https://example.org/paper"))


LIST_RECORDS_XML = """<?xml version="1.0"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"
         xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
  <request verb="ListRecords">/oai</request>
  <ListRecords>
    <record><header><identifier>oai:a:p1</identifier><datestamp>2026-11-01</datestamp></header>
      <metadata><oai_dc:dc>
        <dc:title>题名一</dc:title>
        <dc:identifier>urn:issn:1234</dc:identifier>
        <dc:identifier>https://doi.org/10.1001/One</dc:identifier>
      </oai_dc:dc></metadata></record>
    <record><header status="deleted"><identifier>oai:a:p2</identifier>
      <datestamp>2026-11-02</datestamp></header></record>
    <record><header><identifier>oai:a:p3</identifier><datestamp>2026-11-03</datestamp></header>
      <metadata><oai_dc:dc><dc:title>无 DOI 论文</dc:title>
        <dc:identifier>oai:a:p3</dc:identifier></oai_dc:dc></metadata></record>
    <resumptionToken completeListSize="3" cursor="3">v1-000003</resumptionToken>
  </ListRecords>
</OAI-PMH>"""

ERROR_XML = """<?xml version="1.0"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <request verb="ListRecords">/oai</request>
  <error code="badResumptionToken">令牌过期</error>
</OAI-PMH>"""


class TestParse(unittest.TestCase):
    def test_parse_page(self):
        page = parse_list_records(LIST_RECORDS_XML)
        self.assertEqual(len(page.records), 3)
        self.assertEqual(page.resumption_token, "v1-000003")
        self.assertEqual(page.complete_list_size, 3)
        r1, r2, r3 = page.records
        self.assertEqual(r1.oai_id, "oai:a:p1")
        self.assertEqual(r1.doi_raw, "https://doi.org/10.1001/One")
        self.assertEqual(normalize_doi(r1.doi_raw), "10.1001/one")
        self.assertTrue(r2.deleted)
        self.assertEqual(r2.title, "")
        self.assertIsNone(r3.doi_raw)
        self.assertEqual(r3.title, "无 DOI 论文")

    def test_parse_error(self):
        with self.assertRaises(OaiError) as ctx:
            parse_list_records(ERROR_XML)
        self.assertEqual(ctx.exception.code, "badResumptionToken")

    def test_url_builder(self):
        self.assertIn("metadataPrefix=oai_dc", build_list_records_url("http://h/oai"))
        self.assertIn("resumptionToken=t1", build_list_records_url("http://h/oai", "t1"))


if __name__ == "__main__":
    unittest.main()
