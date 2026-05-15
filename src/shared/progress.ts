/**
 * Shared progress-notification data.
 * Used by all channels — no TUI-specific imports allowed here.
 */

export const STACKOWL_SPINNER = ["·", "◌", "◍", "◉", "✳", "✶"] as const;

/** Yellow → red → yellow fade palette for the thinking animation. */
export const FADE_COLORS = [
  "#F5A623", "#F59020", "#F57418", "#F55810",
  "#FF4444",
  "#F55810", "#F57418", "#F59020",
] as const;

/** Interval between language rotations (ms). */
export const LANG_INTERVAL_MS = 4000;

/** "Working on it..." in 100 languages. */
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
  "Sedang bekerja...",
  "Nagtatrabaho sa ito...",
  "এটি নিয়ে কাজ করছি...",
  "ਇਸ 'ਤੇ ਕੰਮ ਕਰ ਰਿਹਾ ਹਾਂ...",
  "இதில் வேலை செய்கிறேன்...",
  "దీనిపై పని చేస్తున్నాను...",
  "यावर काम करत आहे...",
  "اس پر کام کر رہا ہوں...",
  "در حال کار روی آن هستم...",
  "በዚህ ላይ እየሰራሁ ነው...",
  "Mo n ṣiṣẹ lori rẹ...",
  "Ina aiki akan shi...",
  "Ana m arụ ọrụ na ya...",
  "Ngisebenza kulo...",
  "Ndisebenza kulo...",
  "Werk daaraan...",
  "Treballant en això...",
  "Horretan lanean nago...",
  "Traballando niso...",
  "Yn gweithio ar hynny...",
  "Ag obair air...",
  "Er að vinna í því...",
  "Dirbu ties tuo...",
  "Strādāju pie tā...",
  "Töötan selle kallal...",
  "Delam na tem...",
  "Pracujem na tom...",
  "Radim na tome...",
  "Радим на томе...",
  "Работам на тоа...",
  "Работя по това...",
  "Po punoj për të...",
  "ვმუშაობ ამაზე...",
  "Աշխատում եմ դրա վրա...",
  "Жұмыс жасап жатырмын...",
  "Bu ustida ishlayapman...",
  "Bu üstünde işleýärin...",
  "Иштеп жатам...",
  "Дар ин кор мекунам...",
  "Үүн дээр ажиллаж байна...",
  "यसमा काम गर्दैछु...",
  "ဒါကို လုပ်နေသည်...",
  "កំពុងធ្វើការលើវា...",
  "ກຳລັງດຳເນີນການ...",
  "正在處理...",
  "Lagi nggarap iki...",
  "Keur ngerjakeun ieu...",
  "Nagbuhat niini...",
  "Waxaan u shaqeynayaa...",
  "Miasa amin'izany aho...",
  "Ke a sebetsa ho sona...",
  "Ndikugwira ntchito pa ichi...",
  "Ndimo gukora kuri iyo...",
  "Ndiri kushanda pari zvino...",
  "Laborante pri ĝi...",
  "Opere incumbo...",
  "अस्मिन् कार्यं करोमि...",
  "Qed naħdem fuqha...",
  "Schaffe drun...",
  "O labourat war se...",
  "Trabalhando...",
  "Kwa hili ninafanya kazi...",
  "Mimi ni kufanya kazi...",
  "Está se a trabalhar...",
  "Treballem en això...",
  "Je travaille dessus...",
  "Ich bin dran...",
  "Sto lavorando...",
  "Estoy en ello...",
  "На этом работаю...",
  "처리 중입니다...",
  "考えています...",
] as const;

/** Returns a random "Working on it…" phrase from the 100-language pool. */
export function pickRandomPhrase(): string {
  return THINKING_MESSAGES[Math.floor(Math.random() * THINKING_MESSAGES.length)]!;
}

/**
 * Short human-readable status phrases per tool name.
 * Used by all channels during tool execution.
 */
export const TOOL_STATUS_PHRASES: Record<string, string> = {
  shell:                  "🐚 Running command…",
  read_file:              "📄 Reading file…",
  write_file:             "✏️  Writing file…",
  web_fetch:              "🌐 Fetching page…",
  web_search:             "🔍 Searching the web…",
  browser_navigate:       "🌐 Navigating browser…",
  browser_control:        "🖥️  Controlling browser…",
  read_logs:              "📋 Reading logs…",
  memory_search:          "🧠 Searching memory…",
  memory_write:           "🧠 Writing to memory…",
  list_files:             "📁 Listing files…",
  grep:                   "🔍 Searching files…",
  image_generate:         "🎨 Generating image…",
  calendar_event:         "📅 Checking calendar…",
  email_send:             "📧 Sending email…",
};

/** Returns a tool status phrase, or a generic fallback for unknown tools. */
export function getToolStatusPhrase(toolName: string): string {
  return TOOL_STATUS_PHRASES[toolName] ?? "⚙️  Working…";
}
