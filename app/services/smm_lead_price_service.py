"""
SMM 1#铅锭参考价：抓取、入库与历史查询。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.database import get_conn
from app.services.smm_lead_scraper import (
    SmmLeadReferenceQuote,
    SmmLeadScraperError,
    fetch_smm_lead_reference_price,
)

logger = logging.getLogger(__name__)


def _row_to_dict(row: tuple) -> Dict[str, Any]:
    return {
        "id": int(row[0]),
        "产品": row[1],
        "最低价": float(row[2]),
        "最高价": float(row[3]),
        "均价": float(row[4]),
        "单位": row[5],
        "定价日期": row[6].isoformat() if row[6] else None,
        "数据来源": row[7],
        "抓取时间": row[8].isoformat() if row[8] else None,
        "备注": "公开页区间价按 (最低价+最高价)/2 折算，非 SMM 官方授权数据",
    }


def _upsert_quote(quote: SmmLeadReferenceQuote) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pd_smm_lead_reference_prices
                (product_name, price_low, price_high, average_price, unit, quote_date, source_url, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    product_name = VALUES(product_name),
                    price_low = VALUES(price_low),
                    price_high = VALUES(price_high),
                    average_price = VALUES(average_price),
                    unit = VALUES(unit),
                    source_url = VALUES(source_url),
                    fetched_at = NOW()
                """,
                (
                    quote.product_name,
                    quote.price_low,
                    quote.price_high,
                    quote.average_price,
                    quote.unit,
                    quote.quote_date,
                    quote.source_url,
                ),
            )
            cur.execute(
                """
                SELECT id FROM pd_smm_lead_reference_prices WHERE quote_date = %s
                """,
                (quote.quote_date,),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("写入 SMM 参考价后未找到记录")
            return int(row[0])


def sync_smm_lead_reference_price() -> Dict[str, Any]:
    """抓取公开页并 upsert（同一定价日期仅保留一条，重复同步会更新）。"""
    quote = fetch_smm_lead_reference_price()
    row_id = _upsert_quote(quote)
    data = _row_to_dict(
        (
            row_id,
            quote.product_name,
            quote.price_low,
            quote.price_high,
            quote.average_price,
            quote.unit,
            quote.quote_date,
            quote.source_url,
            datetime.now(),
        )
    )
    return {
        "code": 200,
        "msg": "已同步 SMM 1#铅锭参考价",
        "data": data,
    }


def get_latest_smm_lead_reference_price() -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, product_name, price_low, price_high, average_price, unit,
                       quote_date, source_url, fetched_at
                FROM pd_smm_lead_reference_prices
                ORDER BY quote_date DESC, id DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if not row:
                return None
            return _row_to_dict(row)


def list_smm_lead_reference_prices(
    *,
    page: int = 1,
    page_size: int = 20,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    if page < 1:
        raise ValueError("page 必须 >= 1")
    page_size = min(max(page_size, 1), 500)

    d_from: Optional[date] = None
    d_to: Optional[date] = None
    if date_from:
        d_from = date.fromisoformat(str(date_from).strip())
    if date_to:
        d_to = date.fromisoformat(str(date_to).strip())

    conditions: List[str] = ["1=1"]
    params: List[Any] = []
    if d_from is not None:
        conditions.append("quote_date >= %s")
        params.append(d_from)
    if d_to is not None:
        conditions.append("quote_date <= %s")
        params.append(d_to)
    where_sql = " AND ".join(conditions)
    offset = (page - 1) * page_size

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM pd_smm_lead_reference_prices WHERE {where_sql}",
                tuple(params),
            )
            total = int(cur.fetchone()[0])
            cur.execute(
                f"""
                SELECT id, product_name, price_low, price_high, average_price, unit,
                       quote_date, source_url, fetched_at
                FROM pd_smm_lead_reference_prices
                WHERE {where_sql}
                ORDER BY quote_date DESC, id DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params) + (page_size, offset),
            )
            rows = [_row_to_dict(r) for r in cur.fetchall()]

    return {
        "code": 200,
        "data": {
            "total": total,
            "list": rows,
            "page": page,
            "page_size": page_size,
        },
    }


def run_scheduled_smm_lead_sync() -> None:
    """供 APScheduler 调用的同步入口。"""
    try:
        result = sync_smm_lead_reference_price()
        logger.info(
            "SMM 1#铅锭参考价定时同步成功 quote_date=%s 均价=%s",
            result["data"].get("定价日期"),
            result["data"].get("均价"),
        )
    except SmmLeadScraperError as e:
        logger.warning("SMM 1#铅锭参考价定时同步失败（抓取）: %s", e)
    except Exception:
        logger.exception("SMM 1#铅锭参考价定时同步失败")


def needs_smm_lead_sync_today() -> bool:
    """当日是否尚未成功抓取过（按 fetched_at 日历日判断）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM pd_smm_lead_reference_prices
                WHERE fetched_at IS NOT NULL AND DATE(fetched_at) = CURDATE()
                LIMIT 1
                """
            )
            return cur.fetchone() is None


def startup_smm_lead_sync_if_needed() -> None:
    """服务启动时：若今日尚未抓取，补跑一次（避免等到次日定时任务）。"""
    try:
        if needs_smm_lead_sync_today():
            run_scheduled_smm_lead_sync()
    except Exception:
        logger.exception("SMM 1#铅锭参考价启动补同步失败")
