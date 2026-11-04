// 食物识别 + 饮食记录接口
import http, { get, patch, post } from './http'

/** 低置信度 / 易混淆时的候选菜 */
export interface MatchCandidate {
  name: string
  category?: string | null
  common_portion_g: number
  calories_per_100g?: number | null
  match_method: string   // exact / synonym / alias / contains / fuzzy / confusable
  score: number
  distinguish_tip?: string | null
  relation_type?: string | null
  group_key?: string | null
}

/** 匹配判定：是否需要用户确认、候选、原因 */
export interface MatchInfo {
  query: string
  matched: boolean
  matched_name?: string | null
  method: string
  method_label: string
  score: number
  needs_confirm: boolean
  reason_codes: string[]
  reasons: string[]
  candidates: MatchCandidate[]
  nutrition_source?: string
  corrected?: boolean
  dietary_tags?: string[]
  taste_tags?: string[]
  ingredients?: string[]
}

export interface DishItem {
  dish_name: string
  category?: string
  size_class?: string
  portion_g: number
  portion_desc?: string
  confidence: number
  ingredients: { name: string; portion_g: number }[]
  nutrition: {
    calories: number
    protein_g: number
    fat_g: number
    carb_g: number
    matched: boolean
    matched_name?: string | null
    note: string
  }
  match?: MatchInfo
  corrected?: boolean
  dietary_tags?: string[]
  taste_tags?: string[]
  warnings?: string[]
}

export interface Recognition {
  id: number
  image_url: string | null
  provider: string
  dish_name: string
  calories: number
  confidence: number
  is_food: boolean
  food_record_id: number | null
  created_at: string
  result: {
    vision: {
      dish_name: string
      portion_g: number
      portion_g_raw?: number
      portion_desc: string
      category?: string
      size_class?: string
      basis?: string
      corrected?: boolean
      ingredients: { name: string; portion_g: number }[]
      description: string
    }
    nutrition: {
      calories: number
      protein_g: number
      fat_g: number
      carb_g: number
      matched: boolean
      matched_name?: string | null
      match_method?: string
      match_score?: number
      nutrition_source?: string
      note: string
    }
    match?: MatchInfo
    dishes?: DishItem[]
    food_record_ids?: number[]
    dietary_warnings?: string[]
  }
}

/** 营养库菜品（手动校准下拉 + 营养预览用） */
export interface FoodOption {
  name: string
  name_en?: string | null
  category: string
  category_l2?: string | null
  common_portion_g: number
  serving_g?: number | null
  per_100g: {
    calories: number
    protein_g: number
    fat_g: number
    carb_g: number
    fiber_g?: number | null
    sodium_mg?: number | null
    sugar_g?: number | null
  }
  food_type?: string | null
  dish_style?: string | null
  region?: string | null
  cuisine?: string | null
  state?: string | null
  edible_ratio?: number | null
  aliases?: string[]
  relations?: { group_key: string; relation_type: string; name: string; distinguish_tip?: string | null }[]
  verified?: boolean
  active?: boolean
  source?: string | null
  source_type?: string | null
  license?: string | null
  source_url?: string | null
  version?: string | null
}

export interface FoodRecord {
  id: number
  source: string
  dish_name: string
  portion_g: number
  meal_type: string
  calories: number
  protein_g: number
  fat_g: number
  carb_g: number
  image_url: string | null
}

/** 上传图片识别（LLM 较慢，单独 120s 超时） */
export const recognize = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return http.post<Recognition, Recognition>('/recognitions', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}

export const getRecognitions = (page = 1, pageSize = 10) =>
  get<{ total: number; page: number; page_size: number; items: Recognition[] }>('/recognitions', { page, page_size: pageSize })

/** 删除识别记录（未生成饮食记录的草稿才能删） */
export const deleteRecognition = (id: number) => http.delete(`/recognitions/${id}`)

/** 重新识别：复用原图再跑一次模型（视觉模型较慢，单独 120s 超时，否则 30s 默认超时会误报"请求失败"） */
export const rerunRecognition = (id: number) =>
  http.post<Recognition, Recognition>(`/recognitions/${id}/rerun`, null, { timeout: 120000 })

export interface FoodLibraryStats {
  total: number
  total_all: number
  inactive: number
  cuisines: Record<string, number>
  food_types: Record<string, number>
  sources: Record<string, number>
}

/** 营养库菜品清单（校准弹窗下拉与营养预览）；includeInactive 包含 staging */
export const getFoodLibrary = (includeInactive = false) =>
  get<FoodOption[]>('/foods', includeInactive ? { include_inactive: true } : undefined)

export const getFoodLibraryStats = () => get<FoodLibraryStats>('/foods/stats')

/** 手动校准识别结果：只改菜名+克数，营养值由后端按营养库重算 */
export const correctRecognition = (id: number, data: { dish_name: string; portion_g: number; dish_index?: number }) =>
  patch<Recognition>(`/recognitions/${id}`, data)

export const saveRecognition = (id: number, mealType: string) =>
  post<{ food_record_id: number; points_awarded: number }>(`/recognitions/${id}/save`, { meal_type: mealType })

export const getFoodRecords = (date?: string) =>
  get<{ date: string; records: FoodRecord[]; summary: { total_calories: number; total_protein_g: number; total_fat_g: number; total_carb_g: number } }>(
    '/food-records',
    date ? { date } : undefined,
  )

export const createFoodRecord = (data: { dish_name: string; portion_g: number; meal_type: string; calories?: number }) =>
  post<FoodRecord>('/food-records', data)

export const deleteFoodRecord = (id: number) => http.delete(`/food-records/${id}`)
