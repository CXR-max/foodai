// 食物识别 + 饮食记录接口
import http, { get, post } from './http'

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
    vision: { dish_name: string; portion_g: number; portion_desc: string; ingredients: { name: string; portion_g: number }[]; description: string }
    nutrition: { calories: number; protein_g: number; fat_g: number; carb_g: number; matched: boolean; note: string }
  }
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

export const getRecognitions = (page = 1) =>
  get<{ total: number; items: Recognition[] }>('/recognitions', { page })

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
