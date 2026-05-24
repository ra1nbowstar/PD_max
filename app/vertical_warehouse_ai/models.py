from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class VerticalWarehouseAiSnapshotCreate(BaseModel):
    """生成垂直库房 AI 定价分析快照"""

    model_config = ConfigDict(extra="ignore")

    库房id: int = Field(..., ge=1, description="源库房 id")
    标题: Optional[str] = Field(None, description="快照标题")
    口径日期: Optional[str] = Field(
        None,
        description="解析对标价等的上限日 YYYY-MM-DD；默认当天",
    )


class VerticalWarehouseAiSnapshotUpdate(BaseModel):
    """更新垂直库房 AI 分析快照元数据"""

    model_config = ConfigDict(extra="ignore")

    标题: Optional[str] = None
    口径日期: Optional[str] = Field(None, description="设为 null 可清空：传 JSON null")
