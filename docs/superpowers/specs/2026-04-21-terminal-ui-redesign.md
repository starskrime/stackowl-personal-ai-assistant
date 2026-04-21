# Terminal UI Redesign — Neon Accent (Option B)

**Date:** 2026-04-21  
**Scope:** `src/cli/ui.ts`, `src/cli/home.ts`  
**Type:** Visual-only redesign. No logic, key-handling, or structural changes.

---

## Problem

The current Dark Glass UI has five issues:

1. **Invisible frame** — shadow cells are `rgb(28,28,32)` vs `rgb(20,20,24)` — an 8-point difference most terminals render as identical black. The "pixel frame" is not visible.
2. **Flat monochromatic palette** — nearly all text is white/gray/dim on black. Cyan is the only accent. The UI reads as a gray wall.
3. **No input border** — the input zone is defined only by background color (near-black), making it hard to spot.
4. **Poor label/value contrast** — `chalk.dim` labels next to `chalk.white` values are readable but not scannable. DNA bars use `#` and `.` which feel noisy.
5. **Home screen right panel is empty** — wastes ~60% of the opening screen.

---

## Design Decision

**Option B — Neon Accent** was selected after reviewing three options in the visual companion.

Core principle: dark background + a two-color accent system (amber primary, blue secondary) with role-specific colors for success (green) and metadata (purple). Every interactive element (input, badges, section headers) uses the accent consistently so the eye learns the system immediately.

---

## Color Palette

| Role | Color | Hex | Usage |
|------|-------|-----|-------|
| Primary accent | Amber | `#fab387` | Owl badge, input border, section headers, `›` prompt sym |
| Secondary accent | Blue | `#89b4fa` | Model chip, spinner, tool-running state |
| Success | Green | `#a6e3a1` | Completed tools, cost badge, high-mood DNA blocks |
| Metadata | Purple | `#cba6f7` | Turn counter, triggered instincts/memory stats |
| Primary text | Near-white | `#cdd6f4` | Message content, active values |
| Labels | Slate | `#45475a` | Section labels, "You" prefix, stat labels |
| Panel BG | Deep navy | `#0c0c18` | Top bar, input zone background |
| Content BG | Near-black | `#080810` | Body panels |
| Borders | Dark navy | `#1a1a2c` | Panel separator, chip borders |
| Muted | Dark slate | `#2e2e45` | Tool ms timings, DNA empty blocks |

Chalk mapping:
- Amber → `chalk.rgb(250, 179, 135)`
- Blue → `chalk.rgb(137, 180, 250)`
- Green → `chalk.rgb(166, 227, 161)`
- Purple → `chalk.rgb(203, 166, 247)`
- Labels → `chalk.rgb(69, 71, 90)`
- Panel BG → `chalk.bgRgb(12, 12, 24)`
- Content BG → `chalk.bgRgb(8, 8, 16)`

---

## Changes per Component

### Top Bar (`_buildTopBar` in `ui.ts`, `_buildTopBar` in `home.ts`)

**Current:** `🦉 OwlName` in `chalk.white.bold`, model in cyan, stats in gray.

**New:**
- Owl name wrapped in amber background badge: `chalk.bgRgb(250,179,135).rgb(12,12,24).bold(" 🦉 OwlName ")`
- Model in a "chip" style: `chalk.rgb(137,180,250)` with dim brackets `chalk.rgb(46,46,69)("[") ... chalk.rgb(46,46,69)("]")`
- Turn counter in purple: `chalk.rgb(203,166,247)`
- Token count in labels color
- Cost in green: `chalk.rgb(166,227,161)`
- Bottom border: `chalk.rgb(250,179,135)` colored `━` divider (amber, not gray)

### Section Headers (left panel, `_buildLeft`)

**Current:** `D("REASONING") + D(DIV.repeat(...))` — dim text, dim line.

**New:** `AMBER("SECTION") + DIM(" ") + DIM("─".repeat(remaining_width))`  
Format: `chalk.rgb(250,179,135).bold("SECTION") + chalk.rgb(46,46,69)("─".repeat(w))`  
Applied to: OWL MIND, REASONING, DNA headers.

### DNA Bars (`_dnaBar`)

**Current:** `G("#").repeat(v) + D(".").repeat(10-v)` — green hashes, dim dots.

**New:** Solid block chars `█` with per-trait color:
- `challenge`: amber `chalk.rgb(250,179,135)`
- `verbosity`: blue `chalk.rgb(137,180,250)`
- `mood`: green `chalk.rgb(166,227,161)`
- Empty blocks: `chalk.rgb(26,26,44)("█")` — dark navy, barely visible

Function signature unchanged. Add `trait` parameter: `_dnaBar(label: string, val: number, trait: 'challenge' | 'verbosity' | 'mood')`

### Input Zone (`_buildInputLine`, `_buildInputPanel`)

**Current:** `PANEL_BG` background only — no visible border.

**New:**
- Top border row: `chalk.bgRgb(12,12,24)` + amber left edge char `chalk.rgb(250,179,135)("▔".repeat(w))` — a top border line
- Content row: amber `›` prompt symbol, existing cursor logic unchanged
- Bottom border row: same treatment as top
- When locked (thinking): blue spinner + `chalk.rgb(69,71,90)(" thinking — press ESC to stop")`

### Stat Rows (left panel stats)

**Current:** `C("*")` cyan dot, `D("label")` dim label, `W(value)` white value.

**New:**
- Icon: `chalk.rgb(203,166,247)("◆")` purple diamond (or amber `"⚡"` for triggered instincts)
- Label: `chalk.rgb(69,71,90)(label)` — slate
- Value when active: `chalk.rgb(250,179,135).bold(value)` — amber
- Value when zero/none: `chalk.rgb(46,46,69)("—")` — muted dash (replaces "none")

### Tool Tree (`_buildLeft` reasoning section)

**Current:** `"   L "` and `"   + "` branch chars; `G("Y")`, `R("X")`, `C(spinner)` icons.

**New:**
- Branch chars: `chalk.rgb(46,46,69)("  ├")` and `chalk.rgb(46,46,69)("  └")`
- Done icon: `chalk.rgb(166,227,161)("✓")`
- Error icon: `chalk.rgb(243,139,168)("✕")`
- Running icon: `chalk.rgb(137,180,250)(spinner)` — blue spinner
- Tool name: `chalk.rgb(127,132,156)(name)` when done, `chalk.rgb(137,180,250)(name)` when running

### Home Screen Right Panel (`_buildRight` in `home.ts`)

**Current:** Blank — `lines.push({ t: "", v: 0 })` for all rows.

**New:** Centered prompt area + recent sessions list.
- Center row: `"What do you want to work on?"` label + centered input box
- Below center: list of 3 most recent session titles with turn count and relative time
- Sessions data source: `opts.recentSessions` array (already in `HomeOpts` interface — currently unused)
- If no sessions: show nothing (graceful empty state)

### Panel Separator

**Current:** `PANEL_BG(" │ ")` — pipe char on dark background.

**New:** `chalk.rgb(26,26,44)(" │ ")` — same pipe, explicit dark navy color (no background trick).

### Frame

Remove the `_buildFrame` pixel shadow rows entirely (they are invisible anyway). Replace with: nothing — content starts at row 1. This recovers 2 rows of usable terminal height.

---

## Files Changed

| File | Changes |
|------|---------|
| `src/cli/ui.ts` | Color constants, `_buildTopBar`, `_buildLeft`, `_buildInputPanel`, `_buildInputLine`, `_buildShortcuts`, `_dnaBar` (add trait param), `_buildFrame` (remove or simplify) |
| `src/cli/home.ts` | Color constants, `_buildTopBar`, `_buildLeft`, `_buildRight` (add recent sessions), `_buildShortcuts` |

No other files change.

---

## Out of Scope

- Key handling, scroll, streaming, tool call logic — unchanged
- Session data model — unchanged  
- `HomeOpts.recentSessions` field already exists but is unused; this spec uses it
- No new dependencies
