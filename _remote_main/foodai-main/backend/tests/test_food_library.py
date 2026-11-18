import unittest

from app.services.food_text import infer_region, normalize_name
from app.services.llm import foods_data


class FoodLibraryTest(unittest.TestCase):
    def setUp(self):
        foods_data.reload()

    def test_normalize_name(self):
        self.assertEqual(normalize_name(" 一份 宫保鸡丁 "), "宫保鸡丁")
        self.assertEqual(normalize_name("番茄炒蛋（少油）"), "番茄炒蛋(少油)")

    def test_infer_region(self):
        self.assertEqual(infer_region("川"), "大陆")
        self.assertEqual(infer_region("日式"), "日本")
        self.assertEqual(infer_region("轻食"), "通用")

    def test_library_has_eight_cuisines(self):
        stats = foods_data.get_library_stats()
        self.assertGreaterEqual(stats["total"], 100)
        for cuisine in ("川", "鲁", "粤", "苏", "浙", "闽", "湘", "徽"):
            self.assertIn(cuisine, stats["cuisines"])
        self.assertIn("light_meal", stats["food_types"])
        self.assertIn("single_food", stats["food_types"])

    def test_exact_alias_and_contains(self):
        self.assertEqual(foods_data.find_food("宫保鸡丁")["name"], "宫保鸡丁")
        self.assertEqual(foods_data.find_food("番茄炒蛋")["name"], "西红柿炒鸡蛋")
        self.assertEqual(foods_data.find_food("一碗牛肉拉面")["name"], "牛肉拉面")

    def test_fuzzy_match_is_flagged_not_silent(self):
        # 相似匹配允许存在（如 红烧鱼 → 红烧带鱼），但必须标成 fuzzy 由前端让用户确认，
        # 不能悄悄当成精确命中。
        match = foods_data.match_entry("红烧鱼")
        if match is not None:
            self.assertNotEqual(match["method"], "exact")
            self.assertLess(match["score"], 1.0)
        # 库里完全没有的菜名不应该硬配
        self.assertIsNone(foods_data.match_entry("泰式冬阴功汤"))

    def test_confusable_candidates(self):
        names = [item["entry"]["name"] for item in foods_data.find_candidates("红烧肉")]
        self.assertIn("红烧肉", names)
        self.assertIn("东坡肉", names)
        self.assertIn("把子肉", names)
        self.assertIn("红烧排骨", names)

    def test_alias_candidate_first(self):
        candidates = foods_data.find_candidates("番茄炒蛋")
        self.assertEqual(candidates[0]["entry"]["name"], "西红柿炒鸡蛋")
        # 同义词优先于别名：番茄炒蛋 → 西红柿炒鸡蛋（标准名）
        self.assertIn(candidates[0]["match_type"], {"synonym", "alias"})

    def test_nutrition_calculation(self):
        rice = foods_data.find_food("米饭")
        result = foods_data.calc_nutrition(rice, 200)
        self.assertAlmostEqual(result["calories"], 232.0, places=1)
        self.assertAlmostEqual(result["carb_g"], 51.8, places=1)

    def test_category_average(self):
        avg = foods_data.category_average("荤菜")
        self.assertGreater(avg["calories"], 0)


if __name__ == "__main__":
    unittest.main()
