/** Shared spinner constants for all TUI v2 animated components. */
import { colors } from "../theme/tokens.js";

export const STACKOWL_SPINNER = ["·", "◌", "◍", "◉", "✳", "✶"] as const;
export const SPINNER_AMBER = colors.brand;  // #F5A623 — sourced from design token
export const SPINNER_INTERVAL_MS = 80;

/** Spinner interval for tool call cards (ms). Slower than the raw 80ms to cut re-render rate. */
export const TOOL_SPIN_INTERVAL_MS = 150;

/** Slower tick for the thinking indicator spinner (ms). */
export const THINKING_SPIN_INTERVAL_MS = 250;

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
  "Working on it...",           // English
  "Trabajando en ello...",      // Spanish
  "Je m'en occupe...",          // French
  "Ich arbeite daran...",       // German
  "Ci sto lavorando...",        // Italian
  "Trabalhando nisso...",       // Portuguese
  "Работаю над этим...",        // Russian
  "取り組んでいます...",          // Japanese
  "正在处理...",                 // Chinese (Simplified)
  "작업 중...",                  // Korean
  "أعمل على ذلك...",            // Arabic
  "इस पर काम कर रहा हूं...",    // Hindi
  "Üzerinde çalışıyorum...",    // Turkish
  "Bezig met werken...",        // Dutch
  "Pracuję nad tym...",         // Polish
  "Arbetar på det...",          // Swedish
  "Jobber med det...",          // Norwegian
  "Arbejder på det...",         // Danish
  "Työskentelen sen parissa...", // Finnish
  "Εργάζομαι πάνω σε αυτό...", // Greek
  "Pracuji na tom...",          // Czech
  "Dolgozom rajta...",          // Hungarian
  "Lucrez la asta...",          // Romanian
  "Працюю над цим...",          // Ukrainian
  "Đang xử lý...",              // Vietnamese
  "กำลังดำเนินการ...",           // Thai
  "Sedang mengerjakan...",      // Indonesian
  "עובד על זה...",              // Hebrew
  "Ninafanya kazi...",          // Swahili
  "Üzərində işləyirəm...",      // Azerbaijani
  "Sedang bekerja...",          // Malay
  "Nagtatrabaho sa ito...",     // Filipino
  "এটি নিয়ে কাজ করছি...",      // Bengali
  "ਇਸ 'ਤੇ ਕੰਮ ਕਰ ਰਿਹਾ ਹਾਂ...", // Punjabi
  "இதில் வேலை செய்கிறேன்...",   // Tamil
  "దీనిపై పని చేస్తున్నాను...", // Telugu
  "यावर काम करत आहे...",        // Marathi
  "اس پر کام کر رہا ہوں...",    // Urdu
  "در حال کار روی آن هستم...", // Persian
  "በዚህ ላይ እየሰራሁ ነው...",       // Amharic
  "Mo n ṣiṣẹ lori rẹ...",      // Yoruba
  "Ina aiki akan shi...",       // Hausa
  "Ana m arụ ọrụ na ya...",    // Igbo
  "Ngisebenza kulo...",         // Zulu
  "Ndisebenza kulo...",         // Xhosa
  "Werk daaraan...",            // Afrikaans
  "Treballant en això...",      // Catalan
  "Horretan lanean nago...",    // Basque
  "Traballando niso...",        // Galician
  "Yn gweithio ar hynny...",    // Welsh
  "Ag obair air...",            // Irish
  "Er að vinna í því...",       // Icelandic
  "Dirbu ties tuo...",          // Lithuanian
  "Strādāju pie tā...",         // Latvian
  "Töötan selle kallal...",     // Estonian
  "Delam na tem...",            // Slovenian
  "Pracujem na tom...",         // Slovak
  "Radim na tome...",           // Croatian
  "Радим на томе...",           // Serbian
  "Работам на тоа...",          // Macedonian
  "Работя по това...",          // Bulgarian
  "Po punoj për të...",         // Albanian
  "ვმუშაობ ამაზე...",           // Georgian
  "Աշխատում եմ դրա վրա...",    // Armenian
  "Жұмыс жасап жатырмын...",   // Kazakh
  "Bu ustida ishlayapman...",   // Uzbek
  "Bu üstünde işleýärin...",    // Turkmen
  "Иштеп жатам...",             // Kyrgyz
  "Дар ин кор мекунам...",      // Tajik
  "Үүн дээр ажиллаж байна...", // Mongolian
  "यसमा काम गर्दैछु...",        // Nepali
  "ဒါကို လုပ်နေသည်...",         // Burmese
  "កំពុងធ្វើការលើវា...",         // Khmer
  "ກຳລັງດຳເນີນການ...",           // Lao
  "正在處理...",                 // Chinese (Traditional)
  "Lagi nggarap iki...",        // Javanese
  "Keur ngerjakeun ieu...",     // Sundanese
  "Nagbuhat niini...",          // Cebuano
  "Waxaan u shaqeynayaa...",    // Somali
  "Miasa amin'izany aho...",    // Malagasy
  "Ke a sebetsa ho sona...",    // Sesotho
  "Ndikugwira ntchito pa ichi...", // Chichewa
  "Ndimo gukora kuri iyo...",   // Kinyarwanda
  "Ndiri kushanda pari zvino...", // Shona
  "Laborante pri ĝi...",        // Esperanto
  "Opere incumbo...",           // Latin
  "अस्मिन् कार्यं करोमि...",   // Sanskrit
  "Qed naħdem fuqha...",        // Maltese
  "Schaffe drun...",            // Luxembourgish
  "O labourat war se...",       // Breton
  "Trabalhando...",             // Occitan variant
  "Kwa hili ninafanya kazi...", // Swahili (alt)
  "Mimi ni kufanya kazi...",    // Swahili (alt 2)
  "Está se a trabalhar...",     // Galician (alt)
  "Treballem en això...",       // Catalan (alt)
  "Je travaille dessus...",     // French (alt)
  "Ich bin dran...",            // German (alt)
  "Sto lavorando...",           // Italian (alt)
  "Estoy en ello...",           // Spanish (alt)
  "На этом работаю...",         // Russian (alt)
  "처리 중입니다...",             // Korean (alt)
  "考えています...",              // Japanese (alt)
] as const;
