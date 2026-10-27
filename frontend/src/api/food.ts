// 食物识别 + 饮食记录接口
import http, { get, patch, post } from './http'

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
    nutrition: { calories: number; protein_g: number; fat_g: number; carb_g: number; matched: boolean; matched_name?: string | null; note: string }
  }
}

/** 营养库菜品（手动校准下拉 + 营养预览用） */
export interface FoodOption {
  name: string
  category: string
  common_portion_g: number
  per_100g: { calories: number; protein_g: number; fat_g: number; carb_g: number }
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

/** 营养库菜品清单（校准弹窗下拉与营养预览） */
export const getFoodLibrary = () => get<FoodOption[]>('/foods')

/** 手动校准识别结果：只改菜名+克数，营养值由后端按营养库重算 */
export const correctRecognition = (id: number, data: { dish_name: string; portion_g: number }) =>
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
