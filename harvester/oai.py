"""极简 OAI-PMH 2.0 客户端：只支持本项目需要的 ListRecords + oai_dc。

设计要点：
- 网络请求在数据库事务之外执行（等待响应不占库写事务）；
- 网络层异常统一包装为 :class:`OaiNetworkError`，可安全重试续采；
- OAI 协议级 <error> 抛 :class:`OaiError`，badResumptionToken 由上层决策重采。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit, urlunsplit

import requests

from .doi_utils import looks_like_doi

OAI_NS = "http://www.openarchives.org/OAI/2.0/"
OAI_DC_NS = "http://www.openarchives.org/OAI/2.0/oai_dc/"
DC_NS = "http://purl.org/dc/elements/1.1/"
_NS = {"oai": OAI_NS, "oai_dc": OAI_DC_NS, "dc": DC_NS}


class OaiError(Exception):
    """OAI 协议错误（HTTP 200 响应体内的 <error>）。"""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}".rstrip(": "))
        self.code = code
        self.message = message


class OaiNetworkError(Exception):
    """网络/传输层错误（连接失败、超时、非 200、非法 XML）。"""


@dataclass(frozen=True)
class OaiRecord:
    oai_id: str
    title: str
    doi_raw: str | None
    deleted: bool


@dataclass(frozen=True)
class OaiPage:
    records: list[OaiRecord]
    resumption_token: str | None
    complete_list_size: int | None


def _with_query(url: str, params: dict[str, str]) -> str:
    parts = urlsplit(url)
    query = urlencode(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def build_list_records_url(base_url: str, token: str | None = None) -> str:
    if token:
        return _with_query(base_url, {"verb": "ListRecords", "resumptionToken": token})
    return _with_query(
        base_url, {"verb": "ListRecords", "metadataPrefix": "oai_dc"}
    )


def parse_list_records(xml_text: str) -> OaiPage:
    """解析 ListRecords 响应；遇到协议错误抛 :class:`OaiError`。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise OaiNetworkError(f"响应不是合法 XML: {exc}") from exc

    err = root.find("oai:error", _NS)
    if err is not None:
        raise OaiError(err.get("code", "unknown"), (err.text or "").strip())

    list_el = root.find("oai:ListRecords", _NS)
    if list_el is None:
        raise OaiError("badVerb", "响应中缺少 ListRecords 元素")

    records: list[OaiRecord] = []
    for rec in list_el.findall("oai:record", _NS):
        header = rec.find("oai:header", _NS)
        if header is None:
            continue
        oai_id = (header.findtext("oai:identifier", default="", namespaces=_NS) or "").strip()
        deleted = header.get("status") == "deleted"
        title = ""
        doi_raw = None
        if not deleted:
            dc_el = rec.find("oai:metadata/oai_dc:dc", _NS)
            if dc_el is not None:
                title = (
                    dc_el.findtext("dc:title", default="", namespaces=_NS) or ""
                ).strip()
                for ident_el in dc_el.findall("dc:identifier", _NS):
                    candidate = (ident_el.text or "").strip()
                    if candidate and looks_like_doi(candidate):
                        doi_raw = candidate
                        break
        records.append(OaiRecord(oai_id, title, doi_raw, deleted))

    token_el = list_el.find("oai:resumptionToken", _NS)
    token = None
    complete_size: int | None = None
    if token_el is not None:
        if token_el.text and token_el.text.strip():
            token = token_el.text.strip()
        raw_size = token_el.get("completeListSize")
        if raw_size and raw_size.isdigit():
            complete_size = int(raw_size)
    return OaiPage(records, token, complete_size)


class OaiClient:
    def __init__(self, base_url: str, connect_timeout: float, read_timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = (connect_timeout, read_timeout)

    def fetch_page(self, token: str | None = None) -> OaiPage:
        url = build_list_records_url(self.base_url, token)
        try:
            resp = requests.get(
                url,
                timeout=self.timeout,
                headers={"User-Agent": "library-oai-harvester/1.0"},
            )
        except requests.RequestException as exc:
            raise OaiNetworkError(f"请求失败: {exc.__class__.__name__}: {exc}") from exc
        if resp.status_code != 200:
            raise OaiNetworkError(f"上游返回 HTTP {resp.status_code}")
        return parse_list_records(resp.text)
