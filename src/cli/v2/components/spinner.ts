/** Shared spinner constants for all TUI v2 animated components. */
import { colors } from "../theme/tokens.js";

export const STACKOWL_SPINNER = ["·", "◌", "◍", "◉", "✳", "✶"] as const;
export const SPINNER_AMBER = colors.brand;  // #F5A623 — sourced from design token
export const SPINNER_INTERVAL_MS = 80;

/** Yellow → red → yellow fade palette for the thinking animation. */
export const FADE_COLORS = [
  "#F5A623", "#F59020", "#F57418", "#F55810",
  "#FF4444",
  "#F55810", "#F57418", "#F59020",
] as const;

/** Interval between language rotations (ms). */
export const LANG_INTERVAL_MS = 2500;

/** "Working on it..." in 30 languages. */
export const THINKING_MESSAGES = [
  "Working on it...",
  "Trabajando en ello...",
  "Je m'en occupe...",
  "Ich arbeite daran...",
  "Ci sto lavorando...",
  "Trabalhando nisso...",
  "Работаю над этим...",
  "取り組んでいます...",
  "正在处理...",
  "작업 중...",
  "أعمل على ذلك...",
  "इस पर काम कर रहा हूं...",
  "Üzerinde çalışıyorum...",
  "Bezig met werken...",
  "Pracuję nad tym...",
  "Arbetar på det...",
  "Jobber med det...",
  "Arbejder på det...",
  "Työskentelen sen parissa...",
  "Εργάζομαι πάνω σε αυτό...",
  "Pracuji na tom...",
  "Dolgozom rajta...",
  "Lucrez la asta...",
  "Працюю над цим...",
  "Đang xử lý...",
  "กำลังดำเนินการ...",
  "Sedang mengerjakan...",
  "עובד על זה...",
  "Ninafanya kazi...",
  "Üzərində işləyirəm...",
] as const;
