export const CALLBACK_PREFIX = {
  NAV:  "nav:",
  CFG:  "cfg:",
  VCFG: "vcfg:",
  WIZ:  "wiz:",
  FB:   "fb:",
} as const;

export type CallbackPrefix = typeof CALLBACK_PREFIX[keyof typeof CALLBACK_PREFIX];

export const TELEGRAM_LIMITS = {
  MAX_MESSAGE_LENGTH:               4096,
  CHUNK_LENGTH:                     3800,
  MAX_CHUNKS:                       5,
  BOT_MENU_DESCRIPTION_MAX:         256,
  BOT_MENU_DESCRIPTION_TRUNCATE:    253,
  STREAM_FLUSH_INTERVAL_MS:         500,
  MAX_EDIT_FAILURES:                3,
  STREAM_THROTTLE_MS:               1000,
} as const;
