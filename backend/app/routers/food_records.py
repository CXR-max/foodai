"""饮食记录路由：按天查询/手动录入/删除"""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.food_record import FoodRecord
from app.models.user import User
from app.schemas.food_record import FoodRecordIn
from app.services.llm import foods_data

router = APIRouter(prefix="/food-records", tags=["饮食记录"])


def record_dict(r: FoodRecord) -> dict:
    return {
        "id": r.id, "source": r.source, "dish_name": r.dish_name,
        "portion_g": r.portion_g, "portion_desc": r.portion_desc,
        "calories": r.calories, "protein_g": r.protein_g,
        "fat_g": r.fat_g, "carb_g": r.carb_g,
        "meal_type": r.meal_type,
        "image_url": f"/uploads/{r.image_path}" if r.image_path else None,
        "eaten_at": r.eaten_at.isoformat() if r.eaten_at else None,
        "eaten_date": str(r.eaten_date) if r.eaten_date else None,
    }


@router.get("")
def list_records(
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD，不填默认今天"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """某天的饮食记录 + 当日营养汇总"""
    day = date.fromisoformat(date_str) if date_str else date.today()
    records = (db.query(FoodRecord)
               .filter(FoodRecord.user_id == user.id, FoodRecord.eaten_date == day)
               .order_by(FoodRecord.created_at.asc()).all())
    summary = {
        "total_calories": round(sum(r.calories or 0 for r in records), 1),
        "total_protein_g": round(sum(r.protein_g or 0 for r in records), 1),
        "total_fat_g": round(sum(r.fat_g or 0 for r in records), 1),
        "total_carb_g": round(sum(r.carb_g or 0 for r in records), 1),
    }
    return {"date": str(day), "records": [record_dict(r) for r in records], "summary": summary}


@router.post("")
def create_record(
    data: FoodRecordIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """手动录入：营养值优先按营养库自动算，库没收录才用用户填的"""
    matched = foods_data.find_food(data.dish_name)
    if matched:
        nut = foods_data.calc_nutrition(matched, data.portion_g)
    elif data.calories is not None:
        nut = {"calories": data.calories, "protein_g": data.protein_g or 0,
               "fat_g": data.fat_g or 0, "carb_g": data.carb_g or 0}
    else:
        raise HTTPException(status_code=400, detail="该菜品未收录营养库，请手动填写热量")

    record = FoodRecord(
        user_id=user.id, source="manual", dish_name=data.dish_name,
        portion_g=data.portion_g, meal_type=data.meal_type, **nut,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record_dict(record)


@router.delete("/{record_id}")
def delete_record(record_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """删除饮食记录（校验归属）"""
    record = db.get(FoodRecord, record_id)
    if not record or record.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    db.delete(record)
    db.commit()
    return {"ok": True}
