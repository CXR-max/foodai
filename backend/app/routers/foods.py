"""营养库路由：输出菜品清单，供前端手动校准下拉选择与营养预览"""
from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.models.user import User
from app.services.llm import foods_data

router = APIRouter(prefix="/foods", tags=["营养库"])


@router.get("")
def list_foods(
    cuisine: str | None = Query(default=None),
    food_type: str | None = Query(default=None),
    source: str | None = Query(default=None),
    active: bool | None = Query(default=None),
    q: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    user: User = Depends(get_current_user),
):
    """营养库菜品清单（支持按菜系/类型/来源/状态/关键词过滤）"""
    foods = foods_data.load_all_foods() if include_inactive else foods_data.load_foods()
    if cuisine:
        foods = [f for f in foods if f.get("cuisine") == cuisine]
    if food_type:
        foods = [f for f in foods if f.get("food_type") == food_type]
    if source:
        foods = [f for f in foods if f.get("source") == source]
    if active is not None:
        foods = [f for f in foods if bool(f.get("active", True)) == active]
    if q:
        keyword = q.strip().lower()
        foods = [
            f for f in foods
            if keyword in (f.get("name") or "").lower()
            or keyword in (f.get("name_en") or "").lower()
            or any(keyword in str(alias).lower() for alias in f.get("aliases", []))
        ]
    return [
        {
            "name": f["name"],
            "name_en": f.get("name_en"),
            "category": f.get("category", ""),
            "category_l2": f.get("category_l2"),
            "common_portion_g": f.get("common_portion_g", 100),
            "serving_g": f.get("serving_g"),
            "per_100g": f.get("per_100g", {}),
            "food_type": f.get("food_type"),
            "dish_style": f.get("dish_style"),
            "region": f.get("region"),
            "cuisine": f.get("cuisine"),
            "state": f.get("state"),
            "edible_ratio": f.get("edible_ratio"),
            "aliases": f.get("aliases", []),
            "relations": f.get("relations", []),
            "verified": f.get("verified", False),
            "active": f.get("active", True),
            "source": f.get("source"),
            "source_type": f.get("source_type"),
            "license": f.get("license"),
            "source_url": f.get("source_url"),
            "version": f.get("version"),
        }
        for f in foods
    ]


@router.get("/stats")
def library_stats(user: User = Depends(get_current_user)):
    """营养库规模统计（菜系/类型分布）"""
    return foods_data.get_library_stats()
