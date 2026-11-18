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
  // 三用途：plan=方案内打卡（会自动打卡）/ extra=额外加餐 / inspect=只看热量
  suggested_usage?: PhotoUsage
  suggested_label?: string
  suggested_meal_type?: string
  meal_type?: string
  user_chosen?: boolean
  plan_compare?: PlanCompare
}

export type PhotoUsage = 'plan' | 'extra' | 'inspect'

/** 判定失败时给出的出口：每个都是能直接点的动作，不是一句建议 */
export interface PlanCheckOption {
  /** missing_*：只对上一部分时「缺的那几道算不算吃过」，没有 force——菜品对不上不能打卡 */
  key: 'extra' | 'change_meal' | 'inspect' | 'missing_skip' | 'missing_plan'
  label: string
  desc: string
  warning?: string
}

/** 份量偏离：菜品对、份量不对时才叫「份量问题」 */
export interface PlanCheckPortion {
  significant: boolean
  /** false = 照片没拍全，只比了已经对上的那几道 */
  complete: boolean
  direction: 'over' | 'under' | 'none'
  plan_kcal: number
  photo_kcal: number
  kcal_diff: number
  ratio: number
  needs_confirm: boolean
  items: PlanCompare['matches']
  detail: string
}

/** 这餐现在有什么：已打卡吗、已有几条记录、会不会被顶掉 */
export interface PlanCheckExisting {
  checked: boolean
  checkin_state: 'none' | 'pending' | 'done'
  plan_records: number
  manual_records: number
  will_replace_photo: boolean
  note: string
}

/**
 * 方案内打卡的判定结果
 *
 * status 三档：ok 直接打 / partial 要确认（多半是没拍全）/ fail 不能打。
 * fail 时保存按钮要置灰，并把 options 直接摆出来 —— 用户不该猜「为什么不行」。
 */
export interface PlanCheck {
  ok: boolean
  status: 'ok' | 'partial' | 'fail'
  code: string
  meal_type: string
  meal_label: string
  reason: string
  matched_count: number
  plan_count: number
  match_ratio: number
  matches: PlanCompare['matches']
  only_photo: PlanCompare['only_photo']
  only_plan: PlanCompare['only_plan']
  plan_kcal: number
  photo_kcal: number
  portion: PlanCheckPortion
  existing: PlanCheckExisting
  options: PlanCheckOption[]
  /** 只对上一部分时，缺的那几道默认按「没吃」记 */
  missing_default: 'skip' | 'plan'
  suggest_meal: { meal_type: string; label: string; hits: number; plan_count: number } | null
  hint: string
}

export interface SaveRecognitionIn {
  meal_type: string
  usage: PhotoUsage
  /**
   * 只对上一部分时，方案缺的那几道怎么算：
   * skip = 按没吃记（当天热量会偏少）；plan = 按方案克重补记（这餐等于方案）
   * 一道都对不上时这个字段无效——那种情况打卡本身就是失败的
   */
  missing?: 'skip' | 'plan'
  /** 这餐已有手动记录时是否一起替换（默认不动） */
  replace_manual?: boolean
  /** 保存前修正某道菜的克数（识别估重不准时用） */
  overrides?: { index: number; portion_g: number }[]
}

export interface SaveResult {
  ok: boolean
  saved: boolean
  message: string
  usage: string
  food_record_id?: number
  food_record_ids?: number[]
  points_awarded?: number
  meal_checked?: {
    state: 'done' | 'already_done' | 'no_task'
    task_key?: string
    label: string
    points_awarded: number
  } | null
  voided_ids?: number[]
  portion?: PlanCheckPortion | null
  plan_check?: { status: string; code: string; matched: number; plan_count: number } | null
  intake?: {
    calories: number
    protein_g: number
    plan?: Record<string, number> | null
    extra?: Record<string, number> | null
    plan_baseline?: Record<string, number> | null
  }
  // 判定失败（saved=false）时，后端直接把 judge 结果摊平返回
  code?: string
  reason?: string
  status?: string
  options?: PlanCheckOption[]
  missing_default?: 'skip' | 'plan'
  /** 补记的菜名（missing=plan 时有值） */
  filled_items?: string[]
}

/** 照片与方案同餐的比对：同种菜的克重差、方案里有没有这道菜 */
export interface PlanCompare {
  meal_type: string | null
  meal_label: string
  matches: {
    /** 照片里第几道菜（改克数要靠它对回去，不能用数组顺序） */
    dish_index: number
    dish_name: string
    plan_dish: string
    plan_portion_g: number
    photo_portion_g: number
    portion_delta_g: number
    portion_ratio: number | null
    plan_kcal: number
    photo_kcal: number
    kcal_delta: number
  }[]
  only_photo: { dish_index?: number; dish_name: string; portion_g: number; kcal: number }[]
  only_plan: { dish_name: string; portion_g: number; kcal: number }[]
  plan_kcal: number
  photo_kcal: number
  kcal_diff: number
  has_plan_meal: boolean
  suggest_extra: boolean
  hint: string
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

/** 上传图片识别（LLM 较慢，单独 120s 超时）
 *  mealType 传了就能让后端算出「这张照片和方案那餐差多少」；不传按时间猜
 */
export const recognize = (file: File, mealType?: string) => {
  const form = new FormData()
  form.append('file', file)
  return http.post<Recognition, Recognition>('/recognitions', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
    params: mealType ? { meal_type: mealType } : undefined,
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

/** 换一道：同热量档、同餐段的候选菜 */
export interface FoodAlternative {
  name: string
  category: string
  calories: number
  protein_g: number
  common_portion_g: number
  portion_calories: number
  taste_tags: string[]
}

export const getFoodAlternatives = (name: string, mealType?: string, limit = 6) =>
  get<FoodAlternative[]>('/foods/alternatives', {
    name,
    ...(mealType ? { meal_type: mealType } : {}),
    limit,
  })

/** 手动校准识别结果：只改菜名+克数，营养值由后端按营养库重算 */
export const correctRecognition = (id: number, data: { dish_name: string; portion_g: number; dish_index?: number }) =>
  patch<Recognition>(`/recognitions/${id}`, data)

export const saveRecognition = (id: number, payload: SaveRecognitionIn) =>
  post<SaveResult>(`/recognitions/${id}/save`, payload)

/** 保存前判一次「这道菜算不算方案这一餐」；切餐次/切用途都要重算 */
export const planCheck = (id: number, mealType: string) =>
  get<PlanCheck>(`/recognitions/${id}/plan-check?meal_type=${encodeURIComponent(mealType)}`)

export const getFoodRecords = (date?: string) =>
  get<{ date: string; records: FoodRecord[]; summary: { total_calories: number; total_protein_g: number; total_fat_g: number; total_carb_g: number } }>(
    '/food-records',
    date ? { date } : undefined,
  )

export const createFoodRecord = (data: { dish_name: string; portion_g: number; meal_type: string; calories?: number }) =>
  post<FoodRecord>('/food-records', data)

export const deleteFoodRecord = (id: number) => http.delete(`/food-records/${id}`)
