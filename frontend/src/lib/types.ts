export type AgentKey =
  | 'supervisor'
  | 'health_assessment'
  | 'nutrition_knowledge'
  | 'meal_planning'
  | 'validation'
  | 'recommendation'

export const AGENT_LABEL: Record<AgentKey, string> = {
  supervisor: 'Supervisor',
  health_assessment: 'Health',
  nutrition_knowledge: 'Knowledge',
  meal_planning: 'Meal plan',
  validation: 'Validation',
  recommendation: 'Answer',
}

/** Backend agent display names -> graph keys */
export const AGENT_BY_NAME: Record<string, AgentKey> = {
  'Health Assessment Agent': 'health_assessment',
  'Nutrition Knowledge Agent': 'nutrition_knowledge',
  'Meal Planning Agent': 'meal_planning',
  'Validation Agent': 'validation',
  'Recommendation Agent': 'recommendation',
}

export interface AgentEvent {
  type: string
  agent?: string
  ts: number
  tool?: string
  args?: unknown
  ms?: number
  preview?: string
  status?: string
  error?: string | null
  errors?: string[]
  text?: string
  // supervisor
  next?: string
  proposed?: string
  reason?: string
  override?: string | null
  instruction?: string
  objectives?: string[]
  // validation
  verdict?: 'PASS' | 'REJECT' | 'ESCALATE'
  plan_version?: number
  critical?: number
  flags?: string[]
  blocked?: boolean
}

export interface Totals {
  kcal: number; protein_g: number; carbs_g: number; fat_g: number; fiber_g: number
  sugar_g: number; saturated_fat_g: number; sodium_mg: number; potassium_mg: number
  calcium_mg: number; iron_mg: number; vitamin_c_mg: number; vitamin_k_ug: number
}
export interface FoodPortion { food_code: string; food_name: string; grams: number }
export interface Meal { slot: string; name: string; items: FoodPortion[]; notes: string; totals?: Totals | null }
export interface DayPlan { day: number; meals: Meal[]; totals?: Totals | null }
export interface MealPlan { days: DayPlan[]; region: string; notes: string; version: number }

export interface Citation { chunk_id: string; source: string; title: string; url?: string | null; snippet: string }
export interface Recommendation {
  headline: string; summary: string; key_points: string[]; tips: string[]
  citations: Citation[]; follow_up_questions: string[]; disclaimer: string
  plan?: MealPlan | null
}
export interface RiskFlag { code: string; severity: 'info' | 'warning' | 'critical'; message: string }
export interface Health {
  bmr_kcal: number; tdee_kcal: number
  targets: { kcal: number; protein_g: number; carbs_g: number; fat_g: number; fiber_g: number }
  limits: { sodium_mg_max: number; saturated_fat_g_max?: number | null; added_sugar_g_max?: number | null }
  risk_flags: RiskFlag[]; escalation_required: boolean; assumptions: string[]; rationale: string
}
export interface Violation { rule_id: string; severity: string; message: string; location?: string | null }
export interface Validation { plan_version: number; verdict: string; violations: Violation[]; summary: string; checked: string[] }
export interface Evidence { question: string; answer: string; citations: Citation[]; sufficient: boolean }

export interface FinalState {
  status: string
  turn: number
  profile: Profile
  health: Health | null
  recommendation: Recommendation | null
  latest_validation: Validation | null
  evidence: Evidence[]
  decisions: { next: string; proposed: string; reason: string; override: string | null }[]
  agent_runs: { agent: string; status: string; steps: number; ms: number; tokens: number }[]
}

export type Region = 'IN' | 'US'
export interface Profile {
  name?: string | null
  age?: number | null
  sex?: 'male' | 'female' | null
  height_cm?: number | null
  weight_kg?: number | null
  activity: 'sedentary' | 'light' | 'moderate' | 'active' | 'very_active'
  goal: 'lose_weight' | 'maintain' | 'gain_muscle' | 'gain_weight' | 'manage_condition' | 'general_health'
  target_weight_kg?: number | null
  timeline_weeks?: number | null
  region: Region
  diet_pattern: 'omnivore' | 'eggetarian' | 'vegetarian' | 'vegan' | 'pescatarian' | 'jain'
  religious_rules: string[]
  allergies: string[]
  intolerances: string[]
  dislikes: string[]
  conditions: string[]
  medications: string[]
  pregnant: boolean
  breastfeeding: boolean
  meals_per_day: number
  budget: 'low' | 'medium' | 'high'
  cuisine_preferences: string[]
}

export const EMPTY_PROFILE: Profile = {
  activity: 'light', goal: 'general_health', region: 'IN', diet_pattern: 'vegetarian', religious_rules: [],
  allergies: [], intolerances: [], dislikes: [], conditions: [], medications: [], pregnant: false,
  breastfeeding: false, meals_per_day: 3, budget: 'medium', cuisine_preferences: [],
}
