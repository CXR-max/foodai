import unittest

from scripts.nutrition_import.canonical import (
    import_rows,
    normalize_row,
    parse_list,
    parse_number,
    validate_row,
    validate_warnings,
)


class NutritionImportTest(unittest.TestCase):
    def test_parse_helpers(self):
        self.assertEqual(parse_number("12.5 g"), 12.5)
        self.assertEqual(parse_number("1,200"), 1200.0)
        self.assertIsNone(parse_number(""))
        self.assertEqual(parse_list("猪肉|青椒"), ["猪肉", "青椒"])
        self.assertEqual(parse_list('["米饭", "鸡蛋"]'), ["米饭", "鸡蛋"])

    def test_normalize_chinese_headers(self):
        row = {
            "样品名称": "测试菜",
            "食品分类": "荤菜",
            "菜系": "川",
            "每100克热量": "150",
            "蛋白质": "12",
            "脂肪": "8",
            "碳水化合物": "6",
            "标准份": "200",
            "食材": "猪肉|青椒",
            "废弃率": "20",
        }
        values = normalize_row(row, "test", "1", "test", "official")
        self.assertEqual(values["name_zh"], "测试菜")
        self.assertEqual(values["energy_kcal"], 150.0)
        self.assertEqual(values["common_portion_g"], 200.0)
        self.assertAlmostEqual(values["edible_ratio"], 0.8)
        self.assertEqual(values["ingredients"], ["猪肉", "青椒"])
        self.assertEqual(validate_row(values), [])

    def test_macro_energy_mismatch_is_warning(self):
        row = {
            "name_zh": "错误数据",
            "category_l1": "荤菜",
            "energy_kcal": "500",
            "protein_g": "1",
            "fat_g": "1",
            "carb_g": "1",
            "common_portion_g": "200",
        }
        values = normalize_row(row, "test", "1", "test", "official")
        self.assertEqual(validate_row(values), [])
        self.assertTrue(any("macro-energy" in warning for warning in validate_warnings(values)))

    def test_dry_run_import(self):
        rows = [{
            "name_zh": "测试菜",
            "category_l1": "荤菜",
            "energy_kcal": "150",
            "protein_g": "12",
            "fat_g": "8",
            "carb_g": "6",
            "common_portion_g": "200",
        }]
        result = import_rows(rows, "test", "1", "test", dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["valid"], 1)
        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
