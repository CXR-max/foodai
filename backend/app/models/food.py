from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from app.db.session import Base


class Food(Base):
    __tablename__ = "foods"

    id = Column(Integer, primary_key=True)
    source = Column(String(50), nullable=False, default="legacy_json")
    source_id = Column(String(120), nullable=False)
    name_zh = Column(String(120), nullable=False, index=True)
    name_en = Column(String(240))
    name_norm = Column(String(120), index=True)
    food_type = Column(String(20), default="dish", index=True)
    dish_style = Column(String(30), default="家常菜", index=True)
    region = Column(String(30), default="通用", index=True)
    cuisine = Column(String(30), default="家常菜", index=True)
    category_l1 = Column(String(30), default="其他", index=True)
    category_l2 = Column(String(60))
    state = Column(String(20), default="cooked")
    edible_ratio = Column(Float, default=1.0)
    serving_g = Column(Float)
    common_portion_g = Column(Float, default=100)
    energy_kcal = Column(Float)
    protein_g = Column(Float)
    fat_g = Column(Float)
    carb_g = Column(Float)
    fiber_g = Column(Float)
    sodium_mg = Column(Float)
    sugar_g = Column(Float)
    extra_nutrients = Column(JSON)
    ingredients = Column(JSON)
    suitable_meals = Column(JSON)
    taste_tags = Column(JSON)
    dietary_tags = Column(JSON)
    source_type = Column(String(30), default="seed_estimate")
    license = Column(String(120))
    source_url = Column(String(500))
    retrieved_at = Column(DateTime)
    version = Column(String(50))
    verified = Column(Boolean, default=False, index=True)
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_foods_source_source_id"),
        Index("ix_foods_active_lookup", "active", "region", "cuisine", "category_l1", "food_type"),
    )


class FoodAlias(Base):
    __tablename__ = "food_aliases"

    id = Column(Integer, primary_key=True)
    food_id = Column(Integer, ForeignKey("foods.id"), nullable=False, index=True)
    alias = Column(String(120), nullable=False)
    alias_norm = Column(String(120), nullable=False, index=True)
    alias_type = Column(String(30), default="regional")
    source = Column(String(50), default="app_seed")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("food_id", "alias_norm", name="uq_food_alias_food_norm"),
    )


class FoodRelation(Base):
    __tablename__ = "food_relations"

    id = Column(Integer, primary_key=True)
    group_key = Column(String(100), nullable=False, index=True)
    relation_type = Column(String(20), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    name_norm = Column(String(120), nullable=False, index=True)
    canonical_name = Column(String(120))
    canonical_food_id = Column(Integer, ForeignKey("foods.id"), nullable=True)
    distinguish_tip = Column(String(300))
    nutrition_delta = Column(JSON)
    priority = Column(Integer, default=0)
    source = Column(String(50), default="app_seed")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("group_key", "relation_type", "name_norm", name="uq_food_relation_group_name"),
    )


class FoodIngredient(Base):
    __tablename__ = "food_ingredients"

    id = Column(Integer, primary_key=True)
    food_id = Column(Integer, ForeignKey("foods.id"), nullable=False, index=True)
    ingredient_id = Column(Integer, ForeignKey("foods.id"), nullable=True, index=True)
    ingredient_name = Column(String(120), nullable=False, index=True)
    grams = Column(Float)
    state = Column(String(20))
    edible_ratio = Column(Float)
    created_at = Column(DateTime, default=datetime.now)


class FoodRecipe(Base):
    __tablename__ = "food_recipes"

    id = Column(Integer, primary_key=True)
    food_id = Column(Integer, ForeignKey("foods.id"), nullable=False, index=True)
    version = Column(String(50), default="1")
    yield_g = Column(Float)
    cooked_yield_ratio = Column(Float)
    source = Column(String(100))
    created_at = Column(DateTime, default=datetime.now)


class FoodRecipeItem(Base):
    __tablename__ = "food_recipe_items"

    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, ForeignKey("food_recipes.id"), nullable=False, index=True)
    ingredient_id = Column(Integer, ForeignKey("foods.id"), nullable=True, index=True)
    ingredient_name = Column(String(120), nullable=False)
    grams = Column(Float)
    state = Column(String(20))


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id = Column(Integer, primary_key=True)
    source = Column(String(50), nullable=False, index=True)
    version = Column(String(50))
    license = Column(String(120))
    row_count = Column(Integer, default=0)
    checksum = Column(String(64))
    imported_at = Column(DateTime, default=datetime.now)
