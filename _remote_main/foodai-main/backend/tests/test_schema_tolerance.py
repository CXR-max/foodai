"""请求体宽松归一：前端误传事件对象/字符串时不应该 422 或 500"""
import pytest
from pydantic import ValidationError

from app.schemas.food_record import CorrectRecognitionIn


def test_normal_payload():
    data = CorrectRecognitionIn.model_validate({"dish_name": "辣椒炒肉", "portion_g": 390})
    assert data.dish_name == "辣椒炒肉"
    assert data.portion_g == 390
    assert data.dish_index is None


def test_mouse_event_as_dish_index_is_ignored():
    # 模板 @click="openCorrect" 会把 MouseEvent 序列化成 {} 传上来
    data = CorrectRecognitionIn.model_validate(
        {"dish_name": "辣椒炒肉", "portion_g": 390, "dish_index": {}}
    )
    assert data.dish_index is None


def test_string_numbers_are_coerced():
    data = CorrectRecognitionIn.model_validate(
        {"dish_name": " 辣椒炒肉 ", "portion_g": "390", "dish_index": "1"}
    )
    assert data.dish_name == "辣椒炒肉"
    assert data.portion_g == 390
    assert data.dish_index == 1


def test_empty_portion_is_rejected():
    with pytest.raises(ValidationError):
        CorrectRecognitionIn.model_validate({"dish_name": "辣椒炒肉", "portion_g": ""})
