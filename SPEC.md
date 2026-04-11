# StackOwl Neural Brain — Visual Identity Specification

## Concept & Vision

**NeuroSync** — A living, breathing neural visualization that transforms your owl's accumulated knowledge into an mesmerizing organic brain animation. The brain exists in a cosmic void, pulsing with soft amber-gold light as neurons (knowledge nodes) drift and reconnect like fireflies in a neural network. When the owl is young and naive, you see a sparse constellation. As it learns, the constellation blooms into a dense, shimmering cortex of interconnected wisdom.

This isn't a static avatar — it's a **real-time window into your owl's mind**. Every conversation adds a neuron. Every insight creates a synapse. The brain breathes, pulses when thinking, and occasionally flares when the owl has a breakthrough.

---

## Research: Existing Solutions

After extensive research, here are the best options found:

### Graph/Network Visualization Libraries

| Library                                                                                        | Stars | Best For                        | License | Verdict                                   |
| ---------------------------------------------------------------------------------------------- | ----- | ------------------------------- | ------- | ----------------------------------------- |
| **[force-graph](https://github.com/vasturiano/force-graph)**                                   | 3.1k  | Force-directed graphs, data viz | MIT     | ✅ Mature, WebGL support, 2D/3D/VR/AR     |
| **[d3-force](https://github.com/d3/d3-force)**                                                 | 2k    | Force simulation physics        | ISC     | ✅ Best physics engine for organic motion |
| **[canvas-particle-network](https://github.com/JulianLaval/canvas-particle-network)**          | 228   | Simple particle networks        | MIT     | Lightweight (4KB), good reference         |
| **[CLOUDWERX particle-network](https://github.com/CLOUDWERX-DEV/particle-network-background)** | 3     | Interactive backgrounds         | MIT     | Modern UI, control panel                  |
| **[particles.js](https://github.com/VincentGarreau/particles.js)**                             | 30.3k | Background particles            | MIT     | Over-engineered for our use case          |

### Neural/Brain Visualization Projects

| Project                                                                                                             | Type                  | Tech                     | Notes                                   |
| ------------------------------------------------------------------------------------------------------------------- | --------------------- | ------------------------ | --------------------------------------- |
| **[TensorSpace.js](https://github.com/tensorspacejs/tensorspace)**                                                  | Neural network 3D viz | Three.js + TensorFlow.js | Keras/TensorFlow model visualization    |
| **[BrainBrowser](https://brainbrowser.cbrain.mcgill.ca/)**                                                          | Neuroimaging          | WebGL                    | Medical-grade brain surface viewer      |
| **[Neuromancer](https://discourse.threejs.org/t/real-time-3d-visualilzer-for-convolutional-neural-networks/86640)** | CNN visualization     | Svelte + Threlte         | Real-time with ONNX                     |
| **[arogozhnikov 3D NN](https://arogozhnikov.github.io/3d_nn/)**                                                     | NN visualization      | WebGL                    | Good for understanding NN topology      |
| **[jimfleming/neural](https://github.com/jimfleming/neural)**                                                       | Force-directed NN viz | Canvas                   | Simple, 2010 - old but relevant concept |

### Key Libraries for Implementation

| Library         | Use Case                       | CDN    |
| --------------- | ------------------------------ | ------ |
| **Three.js**    | 3D rendering, WebGL            | ✅ yes |
| **PixiJS**      | 2D WebGL, performant           | ✅ yes |
| **force-graph** | Ready-made graph viz           | ✅ yes |
| **d3-force**    | Physics simulation             | ✅ yes |
| **Two.js**      | 2D renderer (SVG/Canvas/WebGL) | ✅ yes |

### Voice Integration Research

| Technology                        | Use                          | Browser Support                     |
| --------------------------------- | ---------------------------- | ----------------------------------- |
| **Web Speech API**                | STT (Speech-to-Text)         | Chrome 25+, Edge 79+, Safari 14.1+  |
| **MediaRecorder API**             | Audio capture                | Chrome 57+, Firefox 25+, Safari 14+ |
| **SpeechSynthesis API**           | TTS                          | Universal                           |
| **Whisper.cpp**                   | Local STT                    | Node.js + WASM                      |
| **TensorFlow.js Speech Commands** | In-browser keyword detection | Chrome 57+                          |

### macOS Native Options

| Framework            | Use                 | Notes        |
| -------------------- | ------------------- | ------------ |
| **WKWebView**        | Embed browser brain | Native shell |
| **AVFoundation**     | Voice recording     | Native audio |
| **Speech Framework** | Native STT          | macOS 10.15+ |
| **AppKit**           | Native UI           | Full control |

### Recommendation

**Phase 1 (Browser)**: Build on **Vanilla Canvas 2D** with custom Brownian motion physics.

- Simpler than force-graph, maximum control over organic aesthetic
- Use `requestAnimationFrame` for 60fps animation
- Custom particle system (not d3-force) for gentle floating motion

**Phase 2 (Voice)**: Web Speech API for STT, TensorFlow.js Speech Commands for wake word

**Phase 3 (macOS)**: WKWebView wrapper + native Speech framework

---

## Design Language

### Aesthetic Direction

**"Cosmic Neural Coral"** — Organic meets digital. Think deep-sea bioluminescent organisms mixed with neural network topology. Soft, warm, alive — not cold or clinical. The void background has subtle depth, like looking into an aquarium of light.

### Color Palette

```
--void-deep:       #08090d        /* cosmic background */
--void-mid:        #0f1219        /* subtle depth layer */
--glow-primary:    #f5a623        /* amber wisdom — core neuron glow */
--glow-secondary:  #e8c547        /* golden highlight */
--glow-tertiary:   #ffd475        /* warm flash / breakthrough */
--synapse-base:    #3d4a5c        /* dormant connection */
--synapse-active:  #7a9cc6        /* active thought pathway */
--synapse-glow:    #a8c5ff        /* bright thought */
--pulse-soft:      rgba(245, 166, 35, 0.15)   /* ambient pulse */
--pulse-bright:    rgba(255, 212, 117, 0.4)   /* thinking pulse */
--text-label:      #8899aa        /* subtle labels */
```

### Typography

- **Display / Labels**: `Space Grotesk` — geometric, slightly technical
- **Data / Stats**: `JetBrains Mono` — clean monospace for metrics
- **Fallback**: `system-ui, sans-serif`

### Spatial System

- Full viewport canvas (100vw × 100vh) for immersive feel
- Brain centered with 60% viewport coverage
- Stats overlay: top-left corner, semi-transparent glass panel
- Connection status: bottom-right, minimal dot indicator

### Motion Philosophy

| State            | Neuron Behavior                          | Synapse Behavior                    | Overall Pulse              |
| ---------------- | ---------------------------------------- | ----------------------------------- | -------------------------- |
| **Idle**         | Gentle Brownian drift, ~0.3px/s          | Dim, slow pulse 4s cycle            | Soft amber glow, 6s breath |
| **Thinking**     | Faster drift ~1.2px/s, slight clustering | Lines brighten to blue, 1.5s pulse  | Warm flash every 2s        |
| **Learning**     | New neurons fade in with pop effect      | New synapses "spark" into existence | Tertiary gold burst        |
| **Breakthrough** | Rare bright flare, expand/contract       | Whole network pulses once           | White-gold supernova flash |
| **Sleeping**     | Very slow drift, 0.1px/s                 | Almost invisible, 10s cycle         | Dim blue-grey, minimal     |

### Visual Assets

- No external images — pure Canvas 2D / WebGL rendering
- Neurons: radial gradient circles with soft glow (`shadowBlur`)
- Synapses: lines with opacity based on "connection strength"
- Background: subtle particle dust (tiny static dots) for depth
- Optional: WebGL particle system for performance with large brains

---

## Brain Anatomy & Growth System

### Neurons (Knowledge Nodes)

Each neuron represents a **learned concept** — a pellet, a fact, a skill acquired, or a significant conversation topic.

| Owl Age                         | Neuron Count | Description                                       |
| ------------------------------- | ------------ | ------------------------------------------------- |
| **Newborn** (0-5 conversations) | 3-8          | Sparse constellation, basic drift                 |
| **Pupil** (5-20)                | 15-40        | Small clusters forming                            |
| **Scholar** (20-100)            | 60-150       | Dense neighborhoods emerge                        |
| **Sage** (100-500)              | 200-500      | Rich web, multiple clusters                       |
| **Oracle** (500+)               | 500-2000     | Universe of thought, requires canvas optimization |

### Synapses (Connections)

Synapses form between neurons that share:

- **Same domain** (tech, science, personal, creative)
- **Same session** (concepts learned together)
- **Frequent co-reference** (mentioned together often)

| Connection Type | Color              | Meaning                        |
| --------------- | ------------------ | ------------------------------ |
| `weak`          | `--synapse-base`   | Once mentioned together        |
| `medium`        | `--synapse-active` | Learned in same session        |
| `strong`        | `--synapse-glow`   | Frequently referenced together |

### Brain Regions (Optional Visual Grouping)

For large brains (200+ neurons), visualize **regional clustering**:

- **Cortex** (outer): Most recent / active learning
- **Hippocampus** (inner): Memory / episodic knowledge
- **Amygdala** (deep core): Emotional / personal preferences
- Regions are invisible boundaries, not literal shapes

---

## Brain Metrics (Observable State)

These metrics are broadcast from the gateway and drive the visualization:

```typescript
interface BrainState {
  // Raw counts
  neuronCount: number; // total knowledge nodes
  synapseCount: number; // total connections
  activeNeurons: number; // recently referenced (last 5 sessions)

  // Learning signals
  conversationsTotal: number;
  pelletsStored: number;
  skillsAcquired: number;
  parliamentSessions: number;

  // Current state
  owlState: "idle" | "thinking" | "learning" | "breakthrough" | "sleeping";
  lastActiveAt: string; // ISO timestamp

  // Growth metrics
  generation: number; // owl DNA generation
  expertiseDomains: Record<string, number>; // domain → expertise level
  challengeLevel: string; // owl's challenge level
}
```

---

## Connection Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    BROWSER CLIENT                        │
│  ┌─────────────────────────────────────────────────┐    │
│  │           NeuroSync Canvas (Canvas 2D)            │    │
│  │   - Neurons (dots) with Brownian motion          │    │
│  │   - Synapses (lines) with opacity/width          │    │
│  │   - Background particle field                     │    │
│  │   - Glass overlay for stats                       │    │
│  └─────────────────────────────────────────────────┘    │
│                          ▲                               │
│                          │ WebSocket                      │
│                          │ (gateway/ws)                  │
└──────────────────────────┼───────────────────────────────┘
                           │
┌──────────────────────────┼───────────────────────────────┐
│                   STACKOWL GATEWAY                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │  CLI Adapter │  │ Telegram     │  │ Slack        │   │
│  └──────────────┘  └──────────────┘  └──────────────┘   │
│                           ▲                               │
│  ┌──────────────────────────────────────────────────┐   │
│  │            BrainSync Protocol                     │   │
│  │  - Broadcasts BrainState every 5s (idle)         │   │
│  │  - Emits "thinking" / "learning" / "breakthrough" │   │
│  │    events immediately on state change            │   │
│  │  - Clients subscribe on connect                   │   │
│  └──────────────────────────────────────────────────┘   │
│                           ▲                               │
│  ┌──────────────────────────────────────────────────┐   │
│  │              OwlBrainStore                         │   │
│  │  - Tracks neuron/synapse counts from pellets      │   │
│  │  - Aggregates from FactStore + KnowledgeGraph     │   │
│  │  - Listens to learning events                     │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

### WebSocket Protocol

**Endpoint**: `ws://localhost:<gateway-port>/brain` (or `/ws/brain`)

**Client → Server:**

```json
{ "type": "subscribe", "owlName": "Noctua" }
{ "type": "unsubscribe" }
{ "type": "ping" }
```

**Server → Client:**

```json
{ "type": "brain_state", "payload": { ...BrainState } }
{ "type": "state_change", "payload": { "owlState": "thinking" } }
{ "type": "neuron_added", "payload": { "concept": "...", "domain": "tech" } }
{ "type": "pong" }
```

### Gateway Port

- Default: 1841 (configurable via `config.gateway.brainPort`)
- Or integrate with existing WebSocket on same port: `/ws/brain` path

---

## Layout & Structure

### Main Canvas View

```
┌──────────────────────────────────────────────────────────────┐
│ ┌─────────────────┐                                         │
│ │ 🦉 Noctua       │                                         │
│ │ Gen 3 • Oracle  │                                         │
│ │                 │                                         │
│ │ ● 342 neurons   │                                         │
│ │ ◌ 1,247 synap.  │         [ BRAIN CANVAS ]               │
│ │ ▲ 12 active     │            ○     ○                      │
│ │                 │         ○  ╱ ╲  ○ ╱                      │
│ │ 💭 thinking...  │        ○───○──○──○                       │
│ │                 │          ╲ ╱   ╲                         │
│ └─────────────────┘            ○   ○                       │
│                                        ○                     │
│                                         ○                   │
│                                                     ● CONNECT│
└──────────────────────────────────────────────────────────────┘
```

### Stats Panel (Glass Overlay)

```
┌──────────────────────────────┐
│  🦉 Noctua                  │
│  Generation 3 • Oracle       │
│                             │
│  ● 342 neurons              │
│  ◌ 1,247 synapses           │
│  ▲ 12 active                │
│                             │
│  📚 89 pellets              │
│  ⚡ 23 skills               │
│  🧠 3 parliament sessions   │
│                             │
│  💭 thinking...             │
│  ─────────────────────────  │
│  Domain Expertise:          │
│  ████████████░░ tech  78%  │
│  ██████████░░░░░░  science│
│  ██████░░░░░░░░░░  creative│
└──────────────────────────────┘
```

### Responsive Strategy

- **Desktop (1024px+)**: Full canvas with side stats panel
- **Tablet (768px-1023px)**: Stats panel as bottom drawer
- **Mobile (<768px)**: Minimal mode — brain in circle, tap for stats overlay

---

## Features & Interactions

### Core Features

1. **Real-time Brain Rendering**
   - Canvas-based neural network visualization
   - 60fps animation target, degrades gracefully to 30fps
   - Neurons drift with Brownian motion + slight attraction to neighbors
   - Synapses pulse based on connection strength

2. **Adaptive Growth**
   - Brain state synced from gateway
   - New neurons fade in with scale animation (0 → 1 over 800ms)
   - New synapses spark with particle burst effect
   - Smooth interpolation when counts change (no jarring jumps)

3. **State Visualization**
   - **Idle**: Slow, peaceful drift, warm amber glow
   - **Thinking**: Faster movement, blue synaptic highlights, ripples from center
   - **Learning**: New nodes pop in, gold flashes
   - **Breakthrough**: Full network pulse, screen flash, particle explosion
   - **Sleeping**: Dim, very slow, blue-grey tint

4. **Connection Status**
   - Bottom-right indicator dot (green = connected, yellow = reconnecting, red = disconnected)
   - Auto-reconnect with exponential backoff

### User Interactions

| Interaction             | Behavior                                              |
| ----------------------- | ----------------------------------------------------- |
| **Hover neuron**        | Show tooltip with concept name, domain, last accessed |
| **Click neuron**        | Highlight all connected synapses, dim others          |
| **Click empty space**   | Deselect, show all                                    |
| **Scroll wheel**        | Zoom in/out (0.5x to 3x range)                        |
| **Drag canvas**         | Pan the brain view                                    |
| **Double-click**        | Reset zoom and pan                                    |
| **Tap neuron (mobile)** | Same as click                                         |

### Edge Cases

- **No connection**: Show "Connect to your owl" prompt with retry button
- **Empty brain (0 neurons)**: Show a single pulsing "seed" neuron with "Awaiting first knowledge..."
- **Disconnected during session**: Show "Reconnecting..." overlay, preserve last frame
- **Very large brain (1000+ neurons)**: Enable clustering, reduce synapse rendering to strongest only

---

## Component Inventory

### `<BrainCanvas>`

Main visualization canvas.

- **Default**: Full viewport, brain centered, particle background
- **Loading**: Subtle shimmer effect
- **Error**: "Unable to connect" message with retry
- **Mobile**: Circular crop with tap interactions

### `<StatsPanel>`

Glass-morphism overlay showing brain metrics.

- **Default**: Semi-transparent dark glass, amber accents
- **Collapsed (mobile)**: Tap to expand
- **Hover**: Slight brightness increase

### `<ConnectionDot>`

Bottom-right status indicator.

- **Connected**: Green dot, subtle pulse
- **Reconnecting**: Yellow dot, faster pulse
- **Disconnected**: Red dot, static

### `<NeuronTooltip>`

Appears on neuron hover.

- Shows: concept name, domain badge, strength, last active
- Fade in 150ms, fade out 100ms

### `<StateIndicator>`

Shows current owl mental state.

- Icon + text: "💭 thinking", "⚡ learning", "✨ breakthrough"
- Positioned below stats panel
- Subtle breathing animation when active

### `<DomainBar>`

Expertise domain progress bars.

- Horizontal bars with gradient fill
- Animate width changes over 500ms

---

## Technical Approach

### Stack

- **Vanilla JS + Canvas 2D** — No framework needed, maximum performance
- **Single HTML file** with embedded CSS/JS for easy deployment
- **WebSocket** for real-time gateway communication
- **LocalStorage** for saving viewport position/zoom preferences

### File Structure

```
brain/
├── index.html          # Standalone brain visualization (open in browser)
├── brain.js            # Core visualization engine
├── connection.js       # WebSocket client
└── styles.css         # All styling
```

### Brain.js Architecture

```javascript
class BrainRenderer {
  constructor(canvas)
  setState(BrainState)      // Update brain data
  setOwlState(state)       // Change animation mode
  addNeuron(concept, domain) // Animate new neuron
  start()                  // Begin animation loop
  stop()                   // Pause animation
  zoom(factor)             // Zoom in/out
  pan(dx, dy)             // Pan view
}
```

### Performance Targets

| Neuron Count | Target FPS | Synapse Rendering     |
| ------------ | ---------- | --------------------- |
| 0-100        | 60fps      | All visible           |
| 100-300      | 50fps      | All visible           |
| 300-500      | 40fps      | Top 50% strength only |
| 500+         | 30fps      | Clustering + top 30%  |

### Browser Compatibility

- Chrome 90+, Firefox 88+, Safari 14+, Edge 90+
- Canvas 2D required (no WebGL fallback needed for v1)

---

## Voice Integration (Future — Tomorrow)

### Architecture for Voice

```
┌─────────────┐    Audio Stream    ┌─────────────┐
│  Browser    │ ──────────────────► │  Gateway    │
│  (MediaRecorder) │              │  (STT)      │
└─────────────┘                    └──────┬──────┘
                                          │ Text
                                          ▼
                                   ┌─────────────┐
                                   │  OwlEngine  │
                                   └──────┬──────┘
                                          │ Text
                                          ▼
                                   ┌─────────────┐
                                   │  TTS (Web)  │ ◄── Optional: speak response
                                   └─────────────┘
```

### Voice Design Notes

- Use browser `MediaRecorder` API for voice capture
- Gateway receives audio, converts to text (OwlEngine processes)
- Response can be: text only, or text + Web Speech API TTS
- Brain visualization: add "speaking" state with mouth/sound wave animation
- Push-to-talk vs continuous listening toggle

---

## macOS/iOS App (Future)

### Considerations

- **WebView wrapper** ( WKWebView / UIWebView ) — reuse brain HTML/JS
- **Native notifications** when owl has breakthroughs
- **Menu bar app** — small floating brain indicator
- **Voice input** via native macOS speech recognition
- **App Kit / SwiftUI** for native shell, WebView for brain content

---

## Milestones

### v1.0 — Browser Brain (Today)

- [ ] Standalone HTML file with Canvas brain
- [ ] Mock data (no real connection yet)
- [ ] Brown motion animation, basic growth visualization
- [ ] Stats overlay panel
- [ ] Basic interactions (hover, zoom, pan)

### v1.1 — Gateway Integration

- [ ] WebSocket connection to gateway
- [ ] Real brain state from OwlBrainStore
- [ ] State change animations
- [ ] Connection status indicator

### v1.2 — Polish

- [ ] Breakthrough flash effect
- [ ] Domain clustering for large brains
- [ ] Mobile responsive layout
- [ ] Performance optimization

### Future

- [ ] Voice input/output integration
- [ ] macOS menu bar app
- [ ] iOS companion app
