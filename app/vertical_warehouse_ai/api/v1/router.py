"""
垂直库房 AI 定价分析 HTTP 接口（独立于 /tl 比价模块）。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.vertical_warehouse_ai.exceptions import VerticalWarehouseAiLLMError
from app.vertical_warehouse_ai.models import (
    VerticalWarehouseAiSnapshotCreate,
    VerticalWarehouseAiSnapshotUpdate,
)
from app.vertical_warehouse_ai.service import (
    VerticalWarehouseAiService,
    get_vertical_warehouse_ai_service,
)

router = APIRouter(prefix="/vertical-warehouse-ai", tags=["垂直库房AI分析"])


@router.get("/analysis", summary="垂直库房 AI 定价分析（实时，调用大模型）")
def vertical_warehouse_ai_analysis(
    warehouse_id: int = Query(..., ge=1, description="源库房 id"),
    as_of_date: Optional[str] = Query(
        None,
        description="口径日期 YYYY-MM-DD；解析对标价、标定价、运费时取该日及以前最新一条",
    ),
    service: VerticalWarehouseAiService = Depends(get_vertical_warehouse_ai_service),
):
    try:
        return service.get_analysis(warehouse_id=warehouse_id, as_of_date=as_of_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except VerticalWarehouseAiLLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/snapshots", summary="生成垂直库房 AI 定价分析快照")
def vertical_warehouse_ai_snapshots_create(
    body: VerticalWarehouseAiSnapshotCreate,
    service: VerticalWarehouseAiService = Depends(get_vertical_warehouse_ai_service),
):
    try:
        return service.create_snapshot(
            warehouse_id=body.库房id,
            title=body.标题,
            as_of_date=body.口径日期,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except VerticalWarehouseAiLLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/snapshots", summary="垂直库房 AI 定价分析快照列表")
def vertical_warehouse_ai_snapshots_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    warehouse_id: Optional[int] = Query(None, ge=1, description="按源库房 id 筛选"),
    service: VerticalWarehouseAiService = Depends(get_vertical_warehouse_ai_service),
):
    try:
        return service.list_snapshots(
            page=page,
            page_size=page_size,
            warehouse_id=warehouse_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/snapshots/{snapshot_id}", summary="垂直库房 AI 定价分析快照详情")
def vertical_warehouse_ai_snapshots_detail(
    snapshot_id: int,
    service: VerticalWarehouseAiService = Depends(get_vertical_warehouse_ai_service),
):
    try:
        return service.get_snapshot_detail(snapshot_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/snapshots/{snapshot_id}", summary="更新垂直库房 AI 分析快照元数据")
def vertical_warehouse_ai_snapshots_update_meta(
    snapshot_id: int,
    body: VerticalWarehouseAiSnapshotUpdate,
    service: VerticalWarehouseAiService = Depends(get_vertical_warehouse_ai_service),
):
    try:
        clear_title = "标题" in body.model_fields_set and body.标题 is None
        clear_as_of = "口径日期" in body.model_fields_set and body.口径日期 is None
        return service.update_snapshot(
            snapshot_id,
            title=body.标题,
            as_of_date=body.口径日期,
            clear_title=clear_title,
            clear_as_of_date=clear_as_of,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/snapshots/{snapshot_id}", summary="删除垂直库房 AI 定价分析快照")
def vertical_warehouse_ai_snapshots_delete(
    snapshot_id: int,
    service: VerticalWarehouseAiService = Depends(get_vertical_warehouse_ai_service),
):
    try:
        return service.delete_snapshot(snapshot_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
