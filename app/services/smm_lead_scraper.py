"""
从 SMM 公开行情页抓取「SMM 1#铅锭」区间价，并按 (最低价 + 最高价) / 2 计算均价。

说明：数据来自 sem.smm.cn 等公开页，非 SMM 官方授权 API，仅供内部参考，不宜作为合同结算依据。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_DEFAULT_SOURCES: Tuple[str, ...] = (
    "https://sem.smm.cn/lead",
    "https://hq.smm.cn/lead",
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# SMM 1#铅锭 | 16350~16500 | 元/吨 | 05-29
_ROW_RE = re.compile(
    r"SMM\s*1#铅锭"
    r"(?:(?!SMM\s*1#铅锭).){0,120}?"
    r"(\d{4,6})\s*[~～\-－—–]\s*(\d{4,6})"
    r"(?:(?!SMM\s*1#铅锭).){0,80}?"
    r"(\d{2}-\d{2})?",
    re.IGNORECASE | re.DOTALL,
)


class SmmLeadScraperError(ValueError):
    """公开页抓取或解析失败。"""


@dataclass(frozen=True)
class SmmLeadReferenceQuote:
    product_name: str
    price_low: Decimal
    price_high: Decimal
    average_price: Decimal
    unit: str
    quote_date: date
    source_url: str

    def to_api_dict(self) -> dict:
        return {
            "产品": self.product_name,
            "最低价": float(self.price_low),
            "最高价": float(self.price_high),
            "均价": float(self.average_price),
            "单位": self.unit,
            "定价日期": self.quote_date.isoformat(),
            "数据来源": self.source_url,
            "备注": "公开页区间价按 (最低价+最高价)/2 折算，非 SMM 官方授权数据",
        }


def _fetch_html(url: str, *, timeout: float = 20.0) -> str:
    req = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except HTTPError as e:
        raise SmmLeadScraperError(f"请求失败 HTTP {e.code}: {url}") from e
    except URLError as e:
        raise SmmLeadScraperError(f"无法访问 {url}: {e.reason}") from e

    for enc in ("utf-8", "gbk", "gb2312"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_mmdd(raw: Optional[str], *, default_year: int) -> date:
    if not raw:
        return date.today()
    mm, dd = raw.split("-", 1)
    return date(default_year, int(mm), int(dd))


def _parse_quote_from_html(html: str, *, source_url: str) -> SmmLeadReferenceQuote:
    match = _ROW_RE.search(html)
    if not match:
        raise SmmLeadScraperError("页面中未找到「SMM 1#铅锭」价格区间")

    low = Decimal(match.group(1))
    high = Decimal(match.group(2))
    if low <= 0 or high <= 0:
        raise SmmLeadScraperError("解析到的价格无效")
    if low > high:
        low, high = high, low

    today = date.today()
    quote_day = _parse_mmdd(match.group(3), default_year=today.year)
    # 年末年初：若解析日期明显在未来，回退一年
    if quote_day > today:
        quote_day = _parse_mmdd(match.group(3), default_year=today.year - 1)

    avg = (low + high) / Decimal(2)
    return SmmLeadReferenceQuote(
        product_name="SMM 1#铅锭",
        price_low=low,
        price_high=high,
        average_price=avg.quantize(Decimal("0.01")),
        unit="元/吨",
        quote_date=quote_day,
        source_url=source_url,
    )


def fetch_smm_lead_reference_price(
  sources: Optional[List[str]] = None,
) -> SmmLeadReferenceQuote:
    """
    依次尝试公开页，解析 SMM 1#铅锭区间价并计算均价。
    """
    urls = list(sources or _DEFAULT_SOURCES)
    errors: List[str] = []
    for url in urls:
        try:
            html = _fetch_html(url)
            quote = _parse_quote_from_html(html, source_url=url)
            logger.info(
                "SMM 1#铅锭参考价: %s~%s 均价=%s 日期=%s 来源=%s",
                quote.price_low,
                quote.price_high,
                quote.average_price,
                quote.quote_date,
                url,
            )
            return quote
        except SmmLeadScraperError as e:
            errors.append(f"{url}: {e}")
            logger.warning("抓取 SMM 铅价失败 %s", errors[-1])

    raise SmmLeadScraperError("；".join(errors) or "所有来源均失败")
