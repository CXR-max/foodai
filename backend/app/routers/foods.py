"""营养库路由：输出菜品清单，供前端手动校准下拉选择与营养预览"""
from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.models.user import User
from app.services.llm import foods_data

router = APIRouter(prefix="/foods", tags=["营养库"])


@router.get("")
def list_foods(user: User = Depends(get_current_user)):
    """营养库全部菜品（名称/类别/标准份/每100g营养），校准与手动录入共用"""
    return [
        {
            "name": f["name"],
            "category": f.get("category", ""),
            "common_portion_g": f.get("common_portion_g", 100),
            "per_100g": f.get("per_100g", {}),
        }
        for f in foods_data.load_foods()
    ]
