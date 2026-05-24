"""垂直库房 AI 定价分析：HTTP 层业务编排与快照 CRUD。"""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict, List, Optional

from app.database import get_conn
from app.services.tl_service import get_tl_service
from app.vertical_warehouse_ai.analysis import run_full_analysis


def _json_cell_to_dict(val: Any) -> Optional[Dict[str, Any]]:
    if val is None:
        return None
    if isinstance(val, dict):
        return val
    if isinstance(val, (bytes, bytearray)):
        val = val.decode("utf-8")
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        return json.loads(s)
    return None


class VerticalWarehouseAiService:
    def _response_payload(
        self, full: Dict[str, Any], *, snapshot_id: Optional[int] = None
    ) -> Dict[str, Any]:
        return {
            "口径日期": full.get("口径日期"),
            "竞品类型id列表": full.get("竞品类型id列表"),
            "竞品类型名称列表": full.get("竞品类型名称列表"),
            "源库房": full.get("源库房"),
            "绑定边列表": full.get("绑定边列表"),
            "绑定间边列表": full.get("绑定间边列表"),
            "自有绑定边列表": full.get("自有绑定边列表"),
            "竞品绑定边列表": full.get("竞品绑定边列表"),
            "ai建议": full.get("ai建议"),
            "llm_model": full.get("llm_model"),
            "llm_parse_error": full.get("llm_parse_error"),
            "snapshot_id": snapshot_id,
        }

    def get_analysis(
        self,
        *,
        warehouse_id: int,
        as_of_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        tl = get_tl_service()
        as_of: Optional[date] = None
        if as_of_date and str(as_of_date).strip():
            as_of = date.fromisoformat(str(as_of_date).strip())
        full = run_full_analysis(tl, warehouse_id, as_of_date=as_of)
        return {
            "code": 200,
            "data": self._response_payload(full, snapshot_id=None),
        }

    def create_snapshot(
        self,
        *,
        warehouse_id: int,
        title: Optional[str] = None,
        as_of_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        tl = get_tl_service()
        as_of: Optional[date] = None
        if as_of_date and str(as_of_date).strip():
            as_of = date.fromisoformat(str(as_of_date).strip())
        full = run_full_analysis(tl, warehouse_id, as_of_date=as_of)
        ctx = full.get("_input_context") or {}
        llm_full = full.get("_llm_result_full") or {}
        wh_name = (ctx.get("源库房") or {}).get("库房名称") or ""

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pd_vertical_warehouse_ai_snapshots (
                        warehouse_id, warehouse_name, title, as_of_date,
                        input_context, llm_result, llm_model
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        warehouse_id,
                        wh_name,
                        (title or "").strip() or None,
                        as_of or tl._pricing_calendar_date(),
                        json.dumps(ctx, ensure_ascii=False, default=str),
                        json.dumps(llm_full, ensure_ascii=False, default=str),
                        full.get("llm_model"),
                    ),
                )
                snap_id = cur.lastrowid
        return {
            "code": 200,
            "msg": "快照已创建",
            "data": self._response_payload(full, snapshot_id=int(snap_id)),
        }

    def list_snapshots(
        self, *, page: int = 1, page_size: int = 20, warehouse_id: Optional[int] = None
    ) -> Dict[str, Any]:
        if page < 1:
            raise ValueError("page 必须 >= 1")
        page_size = min(max(page_size, 1), 200)
        offset = (page - 1) * page_size
        conds = ["1=1"]
        params: List[Any] = []
        if warehouse_id is not None and int(warehouse_id) >= 1:
            conds.append("warehouse_id = %s")
            params.append(int(warehouse_id))
        where_sql = " AND ".join(conds)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) FROM pd_vertical_warehouse_ai_snapshots WHERE {where_sql}",
                    tuple(params),
                )
                total = cur.fetchone()[0]
                cur.execute(
                    f"""
                    SELECT id, warehouse_id, warehouse_name, title, as_of_date, llm_model, created_at
                    FROM pd_vertical_warehouse_ai_snapshots
                    WHERE {where_sql}
                    ORDER BY id DESC
                    LIMIT %s OFFSET %s
                    """,
                    tuple(params + [page_size, offset]),
                )
                rows_out = []
                for r in cur.fetchall():
                    rows_out.append(
                        {
                            "snapshot_id": r[0],
                            "warehouse_id": r[1],
                            "warehouse_name": r[2] or "",
                            "title": r[3],
                            "as_of_date": r[4].isoformat() if r[4] else None,
                            "llm_model": r[5],
                            "created_at": r[6].isoformat() if r[6] else None,
                        }
                    )
        return {
            "code": 200,
            "data": {"total": total, "list": rows_out, "page": page, "page_size": page_size},
        }

    def get_snapshot_detail(self, snapshot_id: int) -> Dict[str, Any]:
        if snapshot_id < 1:
            raise ValueError("snapshot_id 无效")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, warehouse_id, warehouse_name, title, as_of_date,
                           input_context, llm_result, llm_model, created_at
                    FROM pd_vertical_warehouse_ai_snapshots WHERE id = %s
                    """,
                    (snapshot_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("快照不存在")
        ctx = _json_cell_to_dict(row[5]) or {}
        llm_full = _json_cell_to_dict(row[6]) or {}
        ai_sections = {
            "与自己的库房比": llm_full.get("与自己的库房比"),
            "与竞品库房比": llm_full.get("与竞品库房比"),
            "配置价差比": llm_full.get("配置价差比"),
        }
        return {
            "code": 200,
            "data": {
                "snapshot_id": row[0],
                "warehouse_id": row[1],
                "warehouse_name": row[2] or "",
                "title": row[3],
                "口径日期": row[4].isoformat() if row[4] else None,
                "竞品类型id列表": ctx.get("竞品类型id列表"),
                "竞品类型名称列表": ctx.get("竞品类型名称列表"),
                "源库房": ctx.get("源库房"),
                "绑定边列表": ctx.get("绑定边列表"),
                "绑定间边列表": ctx.get("绑定间边列表"),
                "自有绑定边列表": ctx.get("自有绑定边列表"),
                "竞品绑定边列表": ctx.get("竞品绑定边列表"),
                "ai建议": ai_sections,
                "llm_model": row[7],
                "llm_parse_error": llm_full.get("parseError"),
                "created_at": row[8].isoformat() if row[8] else None,
            },
        }

    def update_snapshot(
        self,
        snapshot_id: int,
        *,
        title: Optional[str] = None,
        as_of_date: Optional[str] = None,
        clear_title: bool = False,
        clear_as_of_date: bool = False,
    ) -> Dict[str, Any]:
        if snapshot_id < 1:
            raise ValueError("snapshot_id 无效")
        updates: List[str] = []
        params: List[Any] = []
        if clear_title:
            updates.append("title = NULL")
        elif title is not None:
            updates.append("title = %s")
            params.append((title or "").strip() or None)
        if clear_as_of_date:
            updates.append("as_of_date = NULL")
        elif as_of_date is not None and str(as_of_date).strip():
            updates.append("as_of_date = %s")
            params.append(date.fromisoformat(str(as_of_date).strip()))
        if not updates:
            raise ValueError("无更新字段")
        params.append(snapshot_id)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE pd_vertical_warehouse_ai_snapshots SET {', '.join(updates)} WHERE id = %s",
                    tuple(params),
                )
                if cur.rowcount == 0:
                    raise ValueError("快照不存在")
        return {"code": 200, "msg": "已更新"}

    def delete_snapshot(self, snapshot_id: int) -> Dict[str, Any]:
        if snapshot_id < 1:
            raise ValueError("snapshot_id 无效")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM pd_vertical_warehouse_ai_snapshots WHERE id = %s",
                    (snapshot_id,),
                )
                if cur.rowcount == 0:
                    raise ValueError("快照不存在")
        return {"code": 200, "msg": "已删除"}


_service: Optional[VerticalWarehouseAiService] = None


def get_vertical_warehouse_ai_service() -> VerticalWarehouseAiService:
    global _service
    if _service is None:
        _service = VerticalWarehouseAiService()
    return _service
