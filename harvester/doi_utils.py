"""DOI 归一化工具。

规则（需求约定）：
- 去除 ``https://doi.org/`` 等 URL 前缀（兼容 http / dx.doi.org / doi: 写法）；
- 统一转小写后作为合并键；
- 空值返回 None（无 DOI 记录保留，但不参与 DOI 合并）。
"""
from __future__ import annotations

import re

_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)
# 裸 DOI 形态：10.<注册机构>/<后缀>
_BARE_DOI_RE = re.compile(r"^10\.\S+/\S+$")


def normalize_doi(raw: str | None) -> str | None:
    """把原始 DOI 字符串归一化为合并键。"""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    low = s.lower()
    for prefix in _DOI_PREFIXES:
        if low.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.strip().rstrip(".").lower()
    return s or None


def looks_like_doi(value: str) -> bool:
    """判断 dc:identifier 文本是否承载 DOI（URL、doi: 或裸 DOI）。"""
    low = value.lower().strip()
    if any(low.startswith(p) for p in _DOI_PREFIXES):
        return True
    return bool(_BARE_DOI_RE.match(value.strip()))
