# 03 — Voice conversation spike: speak to StackOwl, hear it answer

- **Date:** 2026-09-12
- **Status:** research only. Nothing has been built, downloaded or run. No model was pulled or executed on the Jetson dev box.
- **Scope:** two-way, English-only voice conversation in the planned "agentic OS" web dashboard, on mobile and desktop browsers. It must be self-hosted and open source, and scale from a Jetson to a Mac, Windows or Linux box, with or without a GPU. The LLM brain stays the existing provider layer and message pipeline.
- **Confidence tags:** **[H]** means several primary sources agree. **[M]** means one primary source, or vendor-published numbers. **[L]** means community or blog evidence, extrapolation, or conflicting sources.
- **Vendor note:** latency numbers published by a framework or model vendor about their own product are marked as such.

---

## Executive summary

1. **Build a streaming cascaded pipeline, not a native speech-to-speech model.** The cascade is VAD → streaming STT → the *existing* StackOwl pipeline → streaming TTS.
   - The open full-duplex models (Moshi, NVIDIA PersonaPlex) feel the most natural, at about 160–205 ms.
   - But they have no tool calling, only 7B-class knowledge, and are English-only persona models that need a GPU.
   - The one open omni model with function calling (Qwen3-Omni) needs about 79 GB of GPU memory in BF16, and its vLLM path does not yet produce audio.
   - StackOwl's value (owls, tools, memory, consent) lives in the text pipeline. A cascade keeps all of it. **[H]**
2. **The fastest measured all-open-model cascade is about 0.5 s voice-to-voice (P50, RTX 5090).** It is a Pipecat reference stack using Nemotron Speech ASR, a local Nemotron 3 Nano and Magpie TTS. The target the industry quotes is about 800 ms median; human turn gaps peak at 0–200 ms. On Apple Silicon a local Pipecat stack measures under 800 ms. CPU-only and Jetson Orin tiers are realistically in the 1.5–4 s range. **[M]**
3. **The real latency risk is StackOwl's own turn, not the speech models.** An agentic turn with thinking and tools takes seconds. Daily notes that tool calls roughly double the LLM part of the budget. Voice mode therefore needs:
   - an immediate spoken acknowledgement;
   - spoken progress, from the existing `ResponseChunk(kind="progress")` stream;
   - sentence-chunked streaming TTS of the answer.

   Consistent with the standing rule, the answer is *not* to disable thinking. **[M]**
4. **Component shortlist, all self-hostable:**
   - **VAD:** Silero VAD (MIT).
   - **End-of-turn:** Smart Turn v3 (BSD-2, ~12 ms on CPU).
   - **STT:** Nemotron Speech Streaming EN 0.6B (NVIDIA GPU); Parakeet TDT 0.6B v2 (CC-BY-4.0); Whisper large-v3-turbo via MLX or faster-whisper; Moonshine v2 streaming (MIT, CPU/edge).
   - **TTS:** Kokoro-82M as the default (Apache-2.0); Chatterbox Turbo (MIT, cloning) or Qwen3-TTS 0.6B (Apache-2.0) for expressiveness; Pocket TTS (CC-BY-4.0, CPU) for GPU-less hosts.
   - **Transport and orchestration:** WebRTC through Pipecat's `SmallWebRTCTransport` (BSD-2, peer-to-peer, no media server).
5. **Licence traps to design around:**
   - **Piper** is now GPL-3.0 (`piper-tts` 1.8.0 on PyPI comes from `OHF-Voice/piper1-gpl`). StackOwl auto-installs it today.
   - **XTTS-v2:** Coqui Public Model License (CPML), non-commercial, and the company is defunct.
   - **F5-TTS weights:** CC-BY-NC.
   - **openWakeWord pre-trained models:** CC BY-NC-SA.
   - **LiveKit turn detector:** licensed for use only inside LiveKit Agents.
   - **NVIDIA Open Model License:** commercial use is allowed, but it is not an OSI licence.
6. **Browser constraints decide the UX more than model choice does:**
   - The mic needs HTTPS and a user gesture.
   - iOS Safari suspends mic and audio when backgrounded or locked.
   - Chromium's echo cancellation ignores Web Audio playback (a WebRTC loopback workaround exists).
   - The Web Speech API streams audio to Google (Chrome) or Apple (Safari) unless Chrome's opt-in `processLocally` mode is used. It violates the self-hosted rule by default and is not recommended.
7. **StackOwl today** has batch (non-streaming) STT: `openai-whisper` in PyTorch, default model `base`.
   - It is used by Telegram voice notes (with confirm-before-send) and TUI Ctrl+R push-to-talk.
   - It has a file-based Piper TTS tool, plus an opt-in OpenAI-compatible cloud TTS backend.
   - It has **no** VAD, streaming, barge-in, spoken replies or WebSocket/WebRTC endpoint.
   - The control plane is a GET-only aiohttp app.

   Existing assets to reuse: the local-first selector pattern and the `answer|progress` streaming chunk kinds.

---

## 1. Pipeline architectures: cascaded vs speech-to-speech (2026)

### 1.1 The three designs in use

| Design | Examples (open weights) | Model latency | Interruptibility | Tool use / reasoning | Fit with StackOwl's text pipeline |
|---|---|---|---|---|---|
| **Full-duplex audio-native.** One model listens and speaks at the same time over parallel audio streams. | Kyutai **Moshi** (7B, CC-BY-4.0); NVIDIA **PersonaPlex-7B-v1** (Jan 2026, MIT code, NVIDIA Open Model License weights) | Moshi 160 ms theoretical, ~200 ms on an L4; PersonaPlex ~205 ms while handling interruptions; ~70 ms speaker switch | Best: natural overlap, backchannels, barge-in. PersonaPlex reports 100% interruption handling on FullDuplexBench | Weak. PersonaPlex's repo documents **no tool/function calling and no external knowledge integration**. 7B knowledge. Loading knowledge into context crowds out audio history | Poor. It would replace the brain rather than wrap it |
| **Thinker–Talker.** A text reasoning core plus a separate streaming speech head. | **Qwen3-Omni-30B-A3B** (Apache-2.0); Qwen2.5-Omni | Qwen3-Omni as low as 211 ms (audio only); Qwen2.5-Omni ~257 ms | Near-duplex, not true full-duplex | Function calling via XML tags, including from audio input | Medium. It is a *second* brain beside the provider layer. Heavy: ~79 GB BF16 minimum; vLLM audio output for Instruct "released in the near future" |
| **Streaming cascade.** VAD/turn detection → streaming STT → text LLM → streaming TTS. | Pipecat + Nemotron/Parakeet + any LLM + Kokoro/Magpie; Kyutai **Unmute** (STT+TTS wrapped around any OpenAI-compatible LLM) | Sub-second end to end. 508 ms P50 measured on an RTX 5090 (§6) | Good with VAD + turn model + cancellation. Overlap and backchannels feel "turn-based" | Full: tools, long context, RAG, and the platform's own consent gates | **Best.** Voice becomes a channel around the existing pipeline |

**Newer full-duplex entrant: Nemotron VoiceChat (NVIDIA).** It is a 12B full-duplex model that unifies ASR, LLM and TTS, and is in *early access* with qualification required. Its licence and tool calling are unstated, so it is not usable for a self-hosted open design today. **[M]**

The research frontier (e.g. RL-tuned turn-taking in PersonaPlex and full-duplex overlap benchmarks) is moving fast. Treat speech-to-speech as a track to re-evaluate every six months. **[M]**

**Qwen3.5-Omni** (March 2026) had not confirmed open weights at its announcement. Do not plan on it. **[L]**

Sources:
- [Moshi repo](https://github.com/kyutai-labs/moshi), [Moshi paper](https://arxiv.org/html/2410.00037v2)
- [PersonaPlex repo](https://github.com/NVIDIA/personaplex), [PersonaPlex overview](https://rywalker.com/research/nvidia-personaplex), [MarkTechPost on PersonaPlex](https://www.marktechpost.com/2026/01/17/nvidia-releases-personaplex-7b-v1-a-real-time-speech-to-speech-model-designed-for-natural-and-full-duplex-conversations/)
- [Sopyła, "Speech-to-Speech Models in 2026"](https://ai.ksopyla.com/posts/voice-to-voice-models-2026-review/)
- [Qwen3-Omni model card](https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Instruct), [Enterprise realtime voice agents tutorial (arXiv 2603.05413)](https://arxiv.org/html/2603.05413v2)
- [Unmute repo](https://github.com/kyutai-labs/unmute)
- [Nemotron VoiceChat EA](https://developer.nvidia.com/nemotron-voicechat-early-access)
- [Awesome Full-Duplex SDM](https://github.com/Ruiqi-Yan/Awesome-Full-Duplex-SDM)
- [Qwen3.5-Omni status (Spheron)](https://www.spheron.network/blog/deploy-qwen3-5-omni-gpu-cloud/)

### 1.2 Verdict for StackOwl

- **Cascade now.** The 2026 review concludes that "for knowledge-grounded voice conversations in production today, the cascade pipeline remains the most practical architecture" ([Sopyła](https://ai.ksopyla.com/posts/voice-to-voice-models-2026-review/)). Pipecat's authors give the same advice: start with the three-model approach ([Daily, June 2025](https://www.daily.co/blog/advice-on-building-voice-ai-in-june-2025/)). **[H]**
- **Unmute is the proof of the "wrap any text LLM" idea.** But it has **no tool calling** (its maintainers suggest wrapping the LLM server), needs ≥16 GB of GPU memory, and is x86_64 only ([Unmute](https://github.com/kyutai-labs/unmute)). Useful as a reference, not as the component. **[M]**
- **Keep a seam for hybrid later.** A full-duplex "talker" could handle backchannels and small talk while delegating real work to the pipeline. Pipecat lets you swap a cascade for a speech-to-speech service without changing app logic ([Daily advice gist](https://gist.github.com/kwindla/f755284ef2b14730e1075c2ac803edcf)). **[M]**

---

## 2. Open-source STT for streaming English

| Model | English accuracy | Streaming? | Hardware | Licence | Notes |
|---|---|---|---|---|---|
| **NVIDIA Nemotron Speech Streaming EN 0.6B** (Jan 2026, updated Mar 2026) | Avg WER 6.93% @1.12 s chunks, 7.07% @0.56 s, 7.67% @0.16 s, 8.43% @0.08 s | **Native, cache-aware** (80/160/560/1120 ms chunks); punctuation and capitalisation | NVIDIA GPU (Ampere/Blackwell/Hopper/Volta; tested V100, A100, A6000, DGX Spark); Linux preferred. Jetson Thor gets it via Riva. Jetson Orin not listed **[M]** | NVIDIA Open Model License (commercial OK, not OSI) | In a vendor stack it produced final transcripts in ~19–27 ms P50. A 40-locale successor, Nemotron-3.5-ASR-Streaming-0.6B, shipped June 2026 |
| **Parakeet TDT 0.6B v2** | Open ASR Leaderboard avg WER **6.05%**; RTFx 3,386 (batch) | Not native streaming (single pass up to 24 min); used behind VAD in segments | NVIDIA GPU; CPU/ONNX via sherpa-onnx; MLX ports for Apple Silicon (v3) | **CC-BY-4.0** | v3 is 25 European languages, also CC-BY-4.0 |
| **Canary-Qwen-2.5B** | Top of leaderboard, avg WER **5.63%**; RTFx 418 | No (≤40 s training clips) | NVIDIA GPU incl. Jetson listed; NeMo | CC-BY-4.0 | Most accurate, slower. Better for post-hoc transcripts than live turns |
| **Whisper large-v3 / large-v3-turbo** (via faster-whisper, whisper.cpp, MLX) | large-v3 ~6.4% avg WER; turbo ~216× real-time | Not native. Streaming by policy (SimulStreaming/AlignAtt, LocalAgreement) in WhisperLiveKit. The older whisper_streaming reported **3.3 s** latency | Everywhere. MLX on Mac. On Jetson, PyPI CTranslate2 wheels are CPU-only (build from source for CUDA); whisper.cpp CUDA works on Orin Nano | MIT (model and runtimes) | The most portable; high hallucination risk on silence, so pair it with VAD |
| **Moonshine v2** (Feb 2026) | Tiny-streaming 12.0% WER, Base 10.07% (English) | **Native streaming** (sliding-window encoder); "sub-200 ms on edge" claimed | CPU/edge: Python, JS/WASM, iOS, Android, Raspberry Pi, Mac/Win/Linux | **MIT** for code and English models (non-English: community non-commercial) | Best CPU-only and browser-side option; accuracy notably below GPU models **[M]** |
| **Voxtral Mini 4B Realtime** (Mistral, Feb 2026) | English short-form WER 4.90% @480 ms delay; delay configurable 80–2400 ms | **Native streaming** | ≥16 GB GPU; vLLM; community MLX/C/Rust ports | **Apache-2.0** | Strong accuracy/latency trade-off, but a 4B model needs a real GPU |
| **Kyutai STT** (1B en/fr; 2.6B en) | "on par with non-streaming SOTA" (chart only) | Native (1B: 0.5 s delay; 2.6B: 2.5 s) **plus built-in semantic VAD** | GPU (Rust server, WebSocket); MLX on Mac/iPhone | Weights CC-BY-4.0; code MIT/Apache | Semantic end-of-turn built in (Rust server only) |

**Leaderboard context.** The Open ASR Leaderboard shows the trade-off: LLM-decoder models (Canary-Qwen, Granite-Speech) have the lowest WER but are slower, while Parakeet CTC/TDT has the highest throughput (RTFx > 2,000). **[H]**

**Jetson data points** are thin:
- WhisperTRT tiny.en took 0.64 s to transcribe 20 s of speech on an Orin Nano, against 0.85 s for faster-whisper.
- Whisper medium runs at ~0.5× real-time on an Orin Nano.
- On CPU, long utterances took "20–30 seconds" and were not viable. **[L]**

Sources:
- [Nemotron Speech Streaming EN 0.6B](https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b), [NVIDIA cache-aware streaming blog](https://huggingface.co/blog/nvidia/nemotron-speech-asr-scaling-voice-agents), [Nemotron-3.5-ASR-Streaming](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
- [Parakeet TDT 0.6B v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2), [Parakeet v3 MLX](https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3), [Canary-Qwen-2.5B](https://huggingface.co/nvidia/canary-qwen-2.5b)
- [Open ASR Leaderboard blog](https://huggingface.co/blog/open-asr-leaderboard), [The Decoder on the leaderboard](https://the-decoder.com/open-asr-leaderboard-tests-more-than-60-speech-recognition-models-for-accuracy-and-speed/), [Northflank STT 2026](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
- [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit), [whisper_streaming](https://github.com/wonyx/whisper_streaming)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [faster-whisper on Jetson forum](https://forums.developer.nvidia.com/t/faster-whisper-with-cuda-11-4/299791), [whisper.cpp CUDA on Orin Nano](https://thomasthelliez.com/blog/run-whisper-cpp-with-cuda-on-jetson-orin-nano-super/), [WhisperTRT](https://github.com/NVIDIA-AI-IOT/whisper_trt), [Jetson Containers quickstart](https://dev.to/vonusma/jetson-containers-quickstart-on-nvidia-jetson-agx-orin-64gb-2ed9)
- [Moonshine v2 paper](https://arxiv.org/html/2602.12241v1), [Moonshine repo](https://github.com/moonshine-ai/moonshine)
- [Voxtral Realtime model card](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602), [Voxtral Realtime paper](https://arxiv.org/html/2602.11298), [Mistral announcement](https://mistral.ai/news/voxtral-transcribe-2/)
- [Kyutai STT](https://kyutai.org/stt/), [delayed-streams-modeling repo](https://github.com/kyutai-labs/delayed-streams-modeling)
- [Riva release notes (Jetson Thor streaming ASR)](https://docs.nvidia.com/deeplearning/riva/user-guide/docs/release-notes.html)

**Runtime for "any hardware": sherpa-onnx.** It runs streaming ASR, VAD and TTS (incl. Parakeet, Moonshine and Kokoro) on onnxruntime across x86, ARM, embedded boards, Android/iOS and WebAssembly, with bindings for 12 languages ([sherpa-onnx docs](https://k2-fsa.github.io/sherpa/onnx/index.html), [WASM VAD + Moonshine demo](https://huggingface.co/spaces/k2-fsa/web-assembly-vad-asr-sherpa-onnx-en-moonshine-tiny)). It is the best single candidate for the CPU and browser tiers. **[M]**

---

## 3. Open-source TTS for natural, low-latency English

| Model | Naturalness | Streaming / time to first audio | Hardware | Licence (commercial?) | Voice cloning |
|---|---|---|---|---|---|
| **Kokoro-82M** | Very good for its size; the community default | Streaming. **~300 ms** first token on GPU; **~3.5 s** on an older i7 CPU; **<1 s** on M3 Pro CPU. 35–100× real-time on a 4060 Ti ([Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI)). Vendor benchmark on Ryzen 7 5700X CPU: 3,658 ms ([Picovoice](https://picovoice.ai/blog/on-device-tts/), vendor-published) **[M]** | CPU, CUDA, MPS, ARM64/Jetson images; MLX and CoreML ports; WebGPU in browser | **Apache-2.0**: yes | No true cloning (fixed voice packs; voice blending) |
| **Chatterbox Turbo** (Resemble AI, Dec 2025) | High; paralinguistic tags ([laugh], [cough]) | Vendor claims ~75 ms latency, 6× real-time on GPU. The CPU benchmark shows 48 s, so it is **GPU-only** in practice | GPU (350M params) | **MIT**: yes | Zero-shot from ~5 s. **Built-in Perth watermark** |
| **Qwen3-TTS** (Jan 2026; 0.6B and 1.7B) | High; voice design from text descriptions | Dual-track streaming; first packet "as low as 97 ms" (vendor) | GPU for 1.7B; 0.6B targets edge **[L]** | **Apache-2.0**: yes | Clone from 3 s |
| **Kyutai Pocket TTS** (Jan 2026, 100M) | Good | Streaming; **~200 ms** to first chunk, ~6× real-time on a MacBook Air M4 CPU using 2 cores (vendor). The Ryzen CPU benchmark measured 1,713 ms **[L: conflicting]** | **CPU**; community WASM ports | Code MIT; weights **CC-BY-4.0, gated**; bans cloning without consent | Clone from a short WAV |
| **Kyutai TTS 1.6B** (Jul 2025) | High | Streams *text in* and audio out (delayed streams); Unmute ~450 ms TTS latency on dedicated GPUs; 32 users @350 ms on one L40S (vendor) | GPU server (Rust), MLX | CC-BY-4.0 weights **[M]** | Voice list; not open cloning |
| **VibeVoice-Realtime-0.5B** (Microsoft) | Good | ~300 ms first audio | GPU | **MIT** | Limited |
| **Orpheus 3B** (Canopy Labs) | Very expressive (emotion tags) | ~200 ms streaming, ~100 ms with input streaming; ~12 GB VRAM | GPU | Apache-2.0 code/weights **but a Llama 3.2 base, so the Llama licence also applies** | Fine-tunes |
| **NeuTTS Air** (Neuphonic, 748M) | Good | Real-time on CPU (Q4 GGUF, ~400–600 MB). The Nano Q4 variant measured 2,952 ms on the Ryzen benchmark | CPU via llama.cpp | **Apache-2.0** | Clone from 3 s |
| **Piper** | Robotic–okay | Fast on CPU/ARM. The Ryzen benchmark measured 1,720 ms first-token-to-speech | Anything, incl. Raspberry Pi/Jetson | **Now GPL-3.0-or-later** (`piper-tts` 1.8.0, 2026-09-04, from OHF-Voice/piper1-gpl); espeak-ng is GPL; per-voice model cards vary | No |
| **Sesame CSM-1B** | Conversational prosody | ~150 ms synthesis-only TTFA claimed; community calls it "very immature, needs a lot of optimization" | GPU | Apache-2.0 | Context-conditioned |
| **F5-TTS** | High | Not real-time-first | GPU | Code MIT; **weights CC-BY-NC**: no | Yes |
| **XTTS-v2** (Coqui; maintained fork `idiap/coqui-ai-TTS`) | Good | ~200 ms-class streaming on GPU **[L]** | GPU | **CPML, non-commercial; Coqui shut down, so no commercial licence can be bought**: no | Yes |

**Voice-cloning implications:**
- Every cloning-capable model above can impersonate a real person from 3–5 s of audio.
- Pocket TTS's terms explicitly forbid cloning "without explicit and lawful consent", and Chatterbox watermarks its output.
- A "JARVIS-like" voice should be a **designed or stock voice**, or a clone of a *consenting* speaker. Never clone a film actor's voice.

**On the vendor CPU benchmark:** in Picovoice's benchmark every open engine exceeded ~1.7 s first-token-to-speech on a Ryzen CPU. The only engine under 200 ms was the vendor's own proprietary Orca. Treat it as vendor-biased but directionally useful: **CPU-only TTS will not reach a sub-second budget without careful streaming.** **[M]**

Sources:
- [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI), [Kokoro MLX](https://github.com/gabrimatic/kokoro-mlx), [Kokoro WebGPU benchmarks](https://quick-tts.com/blog/kokoro-webgpu-benchmarks.html)
- [Picovoice on-device TTS benchmark (vendor)](https://picovoice.ai/blog/on-device-tts/)
- [Chatterbox repo](https://github.com/resemble-ai/chatterbox), [Chatterbox Turbo](https://www.resemble.ai/chatterbox-turbo/)
- [Qwen3-TTS repo](https://github.com/QwenLM/Qwen3-TTS), [Qwen3-TTS report](https://arxiv.org/html/2601.15621)
- [Pocket TTS repo](https://github.com/kyutai-labs/pocket-tts), [Pocket TTS model card](https://huggingface.co/kyutai/pocket-tts), [Kyutai TTS](https://kyutai.org/tts/), [Kyutai TTS/Unmute announcement](https://x.com/kyutai_labs/status/1940767331921416302?lang=en)
- [VibeVoice-Realtime-0.5B](https://huggingface.co/microsoft/VibeVoice-Realtime-0.5B), [VibeVoice realtime docs](https://github.com/microsoft/VibeVoice/blob/main/docs/vibevoice-realtime-0.5b.md)
- [Orpheus repo](https://github.com/canopyai/Orpheus-TTS), [Cerebrium on Orpheus](https://cerebrium.ai/blog/orpheus-tts-how-to-deploy-orpheus-at-scale-for-production-inference)
- [NeuTTS Air GGUF](https://huggingface.co/neuphonic/neutts-air-q4-gguf), [MarkTechPost on NeuTTS Air](https://www.marktechpost.com/2025/10/02/neuphonic-open-sources-neutts-air-a-748m-parameter-on-device-speech-language-model-with-instant-voice-cloning/)
- [piper-tts on PyPI](https://pypi.org/project/piper-tts/), [rhasspy/piper (archived)](https://github.com/rhasspy/piper), [Piper licensing explainer](https://www.cekura.ai/discover/piper-tts)
- [Sesame CSM-1B](https://huggingface.co/sesame/csm-1b), [CSM realtime discussion](https://huggingface.co/sesame/csm-1b/discussions/10), [Spheron S2S guide](https://www.spheron.network/blog/speech-to-speech-gpu-cloud-moshi-sesame-csm-hertz-dev/)
- [F5-TTS repo](https://github.com/swivid/f5-tts), [XTTS-v2 LICENSE](https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt), [XTTS commercial-use explainer](https://localaimaster.com/blog/xtts-coqui-commercial-license), [idiap coqui-ai-TTS fork](https://github.com/idiap/coqui-ai-TTS)

---

## 4. Conversation mechanics

### 4.1 VAD
- **Silero VAD (MIT)** is the de-facto standard. It processes 30 ms+ chunks in under 1 ms on a single CPU thread. Pipecat defaults: `confidence=0.7`, `start_secs=0.2`, `stop_secs=0.2`.
- **TEN VAD** (modified Apache-2.0) claims faster speech-to-silence transitions; it says Silero lags "several hundred milliseconds". Vendor-published. **[M]**
- Sources: [TEN VAD](https://github.com/TEN-framework/ten-vad), [Silero VAD MLX card](https://huggingface.co/mlx-community/silero-vad-v6), [Pipecat speech input](https://docs.pipecat.ai/guides/learn/speech-input)

### 4.2 Semantic end-of-turn detection
Silence alone either cuts people off mid-thought or adds delay. What real systems use:
- **Pipecat Smart Turn v3.** BSD-2-Clause, with open weights, training data and training script. About 8M params on a Whisper-Tiny base; runs on the raw waveform; ~12 ms on a modern CPU. It is Pipecat's default (`LocalSmartTurnAnalyzerV3`). Sources: [Daily announcement](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/), [model card](https://huggingface.co/pipecat-ai/smart-turn-v3), [repo](https://github.com/pipecat-ai/smart-turn). **[H]**
- **Kyutai STT semantic VAD.** Predicts the probability that the user is done; Rust server only ([Kyutai STT](https://kyutai.org/stt/)).
- **LiveKit turn detector.** A text-based end-of-utterance model. Its licence forbids use "on a standalone basis or with any frameworks other than LiveKit Agents" ([LICENSE](https://huggingface.co/livekit/turn-detector/blob/main/LICENSE)). **Exclude it unless LiveKit Agents is adopted.** **[H]**
- **NVIDIA/Daily reference stack.** Finalises on a 200 ms pause, with Smart Turn running in parallel with ASR and synthetic silence padding to force immediate finalisation ([Daily × NVIDIA](https://www.daily.co/blog/building-voice-agents-with-nvidia-open-models/)). **[M]**

### 4.3 Barge-in / interruption
- **Mechanics.** On confirmed user speech, stop playback *at the client* immediately, cancel in-flight LLM/TTS work, then process the new input. This is Pipecat's `enable_interruptions` default ([Pipecat](https://docs.pipecat.ai/guides/learn/speech-input)).
- **False barge-in from the bot's own voice** is the main real-world failure. Use AEC, plus a minimum-duration or minimum-words filter. One iOS voice-mode implementer discards sub-300 ms "utterances" as speaker bleed ([Eddy, iOS Safari audio sessions](https://samueleddy.com/writing/ios-safari-audio-sessions/)). **[M]**
- **StackOwl-specific question.** Does interrupting *speech* also cancel the *task* (tool calls in flight)? That is an owner decision (§ Decisions). It touches the consent and authority model.

### 4.4 Echo cancellation in the browser
- `getUserMedia({audio:{echoCancellation:true}})` gives browser or OS AEC. Chrome can select `browser` or `system` cancellers ([Chrome blog](https://developer.chrome.com/blog/more-native-echo-cancellation)).
- **Chromium does not apply AEC to audio played through the Web Audio API** ([crbug 687574](https://bugs.chromium.org/p/chromium/issues/detail?id=687574)). The documented workaround routes playback through a local WebRTC loopback into an `<audio>` element ([focused.io](https://focused.io/lab/echo-cancellation-with-web-audio-api-and-chromium)).
- **Implication:** receiving TTS as a WebRTC remote track gets AEC "for free". Streaming PCM over WebSocket into Web Audio does not. **[M]**
- The W3C is standardising `echoCancellation` modes (`all` / `remote-only`) ([W3C TAG review](https://tag-github-bot.w3.org/gh/w3ctag/design-reviews-private-brainstorming/163), [MDN](https://developer.mozilla.org/en-US/docs/Web/API/MediaTrackConstraints/echoCancellation)).
- Adaptive AEC also needs a few seconds to converge after the mic opens ([dev.to write-up](https://dev.to/hamedhajiloo/how-i-fixed-a-web-audio-echo-problem-with-a-5-second-delay-384h)). **[L]**

### 4.5 Wake word vs push-to-talk
- **openWakeWord.** Code is Apache-2.0, but **all pre-trained models are CC BY-NC-SA 4.0**, so a StackOwl wake word ("Hey Owl") needs a self-trained model. A browser port runs the models via onnxruntime-web ([openWakeWord](https://github.com/dscripka/openWakeWord), [openwakeword_wasm](https://github.com/dnavarrom/openwakeword_wasm)). **[H]**
- **In a mobile browser, always-on wake word is not viable.** iOS Safari mutes mic capture and suspends WebRTC/Web Audio when locked or backgrounded ([WebKit bug 237878](https://bugs.webkit.org/show_bug.cgi?id=237878), [Apple forum](https://developer.apple.com/forums/thread/774239), [webrtcHacks Safari guide](https://webrtchacks.com/guide-to-safari-webrtc/)). **[H]**
- **What real systems do:** ChatGPT-style voice modes use **tap to start a session, then open-mic with VAD, turn detection and barge-in**. Push-to-hold is the fallback. Wake word is for dedicated devices or kiosks (Home Assistant's stack uses openWakeWord on satellites). **[M]** for the ChatGPT pattern (common practice, no primary source fetched).
- **Recommendation:** tap-to-talk session with open mic on mobile; an optional wake word on a desktop tab left in the foreground.

---

## 5. Transport between browser and self-hosted server

### 5.1 WebRTC vs WebSocket
| | WebRTC | WebSocket |
|---|---|---|
| Network | UDP, jitter buffer, Opus with packet-loss concealment, congestion adaptation; "automatically adjusts to changing network conditions" | TCP, so head-of-line blocking stalls audio on lossy mobile links |
| AEC | Remote track is echo-cancelled in Chromium | Web Audio playback is **not** echo-cancelled in Chromium (§4.4) |
| Complexity | Signalling (offer/answer), STUN; **TURN needed for strict NAT/production** | Trivial; could live on the existing aiohttp control plane |
| Guidance | Pipecat authors: "You shouldn't use WebSockets for edge-to-cloud realtime audio" | Fine for LAN-only prototypes and server-to-server |

Sources: [Daily, "You don't need a WebRTC server"](https://www.daily.co/blog/you-dont-need-a-webrtc-server-for-your-voice-agents/), [SmallWebRTC docs](https://docs.pipecat.ai/api-reference/server/services/transport/small-webrtc), [Daily June 2025 advice](https://www.daily.co/blog/advice-on-building-voice-ai-in-june-2025/). **[M]** (the WebRTC argument comes from WebRTC vendors, though the TCP and AEC facts stand on their own)

### 5.2 Orchestration frameworks
- **Pipecat** (BSD-2-Clause; Daily plus community).
  - A Python pipeline of frames with VAD, Smart Turn, interruptions, 150+ service plugins, and client SDKs for web, iOS and Android ([repo](https://github.com/pipecat-ai/pipecat), [docs](https://docs.pipecat.ai/overview/introduction)).
  - **`SmallWebRTCTransport`** is peer-to-peer with no media server or third-party service. Limited to one human plus one agent, which fits a personal assistant.
  - STUN is needed across networks; TURN on macOS/strict NAT and "recommended" in production; HTTPS is required ([SmallWebRTC](https://docs.pipecat.ai/api-reference/server/services/transport/small-webrtc)).
  - Local Mac examples exist ([kwindla/macos-local-voice-agents](https://github.com/kwindla/macos-local-voice-agents)). **[H]**
- **LiveKit server + LiveKit Agents** (Apache-2.0; Go SFU built on Pion). Fully self-hostable, but it adds a separate media-server service. Its best turn detector is framework-locked ([livekit/livekit](https://github.com/livekit/livekit), [Agents docs](https://docs.livekit.io/agents/)). Better fit if multi-party rooms or SIP telephony are wanted. **[H]**
- **FastRTC** (Gradio). A Python library that exposes a function as WebRTC or WebSocket streams, with built-in `ReplyOnPause` VAD and FastAPI mounting ([repo](https://github.com/gradio-app/fastrtc)). Simpler, with fewer turn-taking features. **[M]**
- **Unmute.** A Next.js front end and FastAPI WebSocket backend using a protocol based on the OpenAI Realtime API ([repo](https://github.com/kyutai-labs/unmute)). A reference for protocol shape.

### 5.3 Mobile browser constraints
- **Secure context:** mic and WebRTC need HTTPS ([SmallWebRTC docs](https://docs.pipecat.ai/api-reference/server/services/transport/small-webrtc); [MDN getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)). A self-hosted box reached from a phone needs a real certificate, e.g. on a private domain or tunnel. **[H]**
- **iOS gesture and autoplay:**
  - Playback must start in a synchronous call chain rooted in a tap, and `AudioContext` must be `resume()`d inside the gesture.
  - Prime `getUserMedia` at session start.
  - Set `navigator.audioSession.type='play-and-record'` (iOS 17+), or TTS may route to the earpiece.
  - Don't use `speechSynthesis` for output.
  - Sources: [Eddy](https://samueleddy.com/writing/ios-safari-audio-sessions/), [webrtcHacks](https://webrtchacks.com/guide-to-safari-webrtc/). **[M]**
- **Backgrounding:** iOS mutes capture and suspends audio when the page is backgrounded or the screen locks ([WebKit 237878](https://bugs.webkit.org/show_bug.cgi?id=237878), [Apple forum 689182](https://developer.apple.com/forums/thread/689182)). Conversations cannot continue in the pocket from a web app. A native wrapper would be needed for that. **[H]**

### 5.4 Web Speech API: flag against the self-hosted rule
- **Chrome** streams audio to Google's servers by default. **Safari** sends speech to Apple and shows a modal saying so. Sources: [MDN Using Web Speech](https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API/Using_the_Web_Speech_API), [addpipe deep dive](https://blog.addpipe.com/a-deep-dive-into-the-web-speech-api/), [Polypane](https://polypane.app/blog/not-all-browser-apis-are-web-apis/). **[H]**
- **Chrome's on-device mode.** `SpeechRecognition` accepts `processLocally: true`, with `available()` / `install()` for language packs, and promises "neither raw audio nor transcriptions leave the user's device". It is Chrome-only, the engine is closed, and it is not a self-hosted open component ([explainer](https://github.com/WebAudio/web-speech-api/blob/main/explainers/on-device-speech-recognition.md), [blink-dev Intent to Ship](https://groups.google.com/a/chromium.org/g/blink-dev/c/VNOok2dbmHM/m/gwbtzV-lAQAJ)). Reported as available from Chrome 139 ([Medium](https://medium.com/@roman_fedyskyi/on-device-speech-uis-in-chrome-139-4b9f0397b9c9)). **[L on version]**
- **Recommendation:** do not use it in the design. At most, an explicitly labelled opt-in with `processLocally` enforced.

---

## 6. Latency budgets

### 6.1 Human targets
- Across 10 languages, response offsets are unimodal and peak **within 0–200 ms** of the end of the question; the cultural spread in the mean stays within ~250 ms ([Stivers et al., PNAS 2009](https://www.pnas.org/doi/10.1073/pnas.0903616106)). **[H]**
- **Voice-agent practice:** aim for **800 ms median voice-to-voice**; 1,500 ms is acceptable for a proof of concept ([Daily June 2025](https://www.daily.co/blog/advice-on-building-voice-ai-in-june-2025/)).
- Daily's budget for hosted services: network ~200 ms, STT+VAD ~400 ms, LLM ~500 ms, TTS ~200 ms. **Tool calls roughly double the LLM share.** **[M]**

### 6.2 Measured open-source cascades
| Hardware | Stack | Measured voice-to-voice | Source / confidence |
|---|---|---|---|
| **RTX 5090** (single user, local) | Nemotron Speech ASR + Nemotron 3 Nano 30B (local) + Magpie TTS, Pipecat + Smart Turn | **P50 508 ms** (415–639). ASR P50 19 ms, LLM 171 ms, TTS 108 ms | [Daily × NVIDIA](https://www.daily.co/blog/building-voice-agents-with-nvidia-open-models/). Vendor-published **[M]** |
| **DGX Spark** | same | **P50 1,180 ms** (759–2,981). LLM P50 750 ms dominates | same **[M]** |
| **M-series Mac** | MLX Whisper + Gemma 3 4B (LM Studio) + Kokoro + Silero + Smart Turn v2, serverless WebRTC | **< 800 ms** | [kwindla/macos-local-voice-agents](https://github.com/kwindla/macos-local-voice-agents) (Pipecat co-creator) **[M]** |
| 4×H100 (multi-user) | NVIDIA Nemotron Voice Agent blueprint | sub-second, up to 64 parallel streams | [NVIDIA blueprint](https://github.com/NVIDIA-AI-Blueprints/nemotron-voice-agent) **[M]** |
| L40S / dedicated GPUs | Kyutai Unmute (STT+TTS) | ~450 ms response in production; ~750 ms on a single GPU | [Unmute](https://github.com/kyutai-labs/unmute) **[M]** |
| **Jetson Orin Nano** | Whisper (tiny–small) + 3B LLM (~15–30 tok/s) + Kokoro/Piper | "whole voice loop takes a few seconds"; Whisper 0.3–0.8 s; whisper.cpp CUDA "a few seconds" for long utterances | [nexline](https://nexline.ai/blog/en/jetson-orin-nano-home-automation/), [Thelliez](https://thomasthelliez.com/blog/building-a-local-robot-brain-on-jetson-orin-nano-super/), [ProventusNova](https://proventusnova.com/blog/llm-inference-jetson-orin-llamacpp-ollama). Community **[L]** |
| **Jetson Thor** | Riva Parakeet streaming ASR + Magpie TTS | Production on-device demo (Caterpillar, CES 2026); no latency figure. **Riva on Orin does not support Magpie TTS or Silero VAD** | [Riva release notes](https://docs.nvidia.com/deeplearning/riva/user-guide/docs/release-notes.html), [Cobus Greyling](https://cobusgreyling.medium.com/nvidia-riva-is-the-speech-layer-that-survived-the-great-rewrite-82cb3453529b) **[M]** |
| Desktop GPU (generic) | Whisper + local LLM + Kokoro | "under 1.5 s" / "1–2 s" | [local-llm.net guide](https://www.local-llm.net/guides/local-voice-assistant/), [InsiderLLM](https://insiderllm.com/guides/voice-chat-local-llms-whisper-tts/) **[L]** |

### 6.3 Proposed budget for StackOwl voice (to be validated by the spikes)

| Segment | GPU box | Apple Silicon | CPU-only / Jetson Orin |
|---|---|---|---|
| Client capture + network (WebRTC, LAN) | 50–100 ms | 50–100 ms | 50–100 ms |
| End of speech → turn decision (VAD 200 ms + Smart Turn) | 200–300 ms | 200–300 ms | 250–400 ms |
| STT final | 20–100 ms (streaming) | 100–300 ms | 300–1,000 ms |
| **StackOwl pipeline to first spoken token** (no tools) | provider TTFT (local 170–750 ms measured; remote: provider-dependent) | same | usually remote LLM |
| TTS first audio | 100–300 ms | 200–500 ms | 700–3,500 ms |
| **Voice-to-voice (no tools)** | **~0.6–1.2 s** | **~0.8–1.5 s** | **~1.5–4 s [L]** |
| Tool-using agentic turn | Seconds to minutes. **Speak an acknowledgement within ~1 s, then narrate progress** | same | same |

---

## 7. What StackOwl has today (read-only grep, 2026-09-12)

| Area | What exists | Engine | Local or remote | Gaps for conversational voice |
|---|---|---|---|---|
| STT substrate | `src/stackowl/media/stt/{base,local,selector}.py`: `WhisperSttBackend`, `SttSelector` (local-first, never raises) | **`openai-whisper`** (PyTorch), pinned `>=20231117,<20250626` in `pyproject.toml`; default model `base` (`TranscriptionSettings.model`) | **Local.** The cloud STT backend is a placeholder that always reports unavailable | Batch only: whole clip in, text out. No streaming, partials, VAD or timestamps. PyTorch Whisper is the slowest Whisper runtime |
| Telegram voice notes | `channels/telegram/voice.py` + `voice_confirm.py`: download OGG → STT → show transcript with ✅/🗑 buttons → inject only on Send | via `SttSelector` | Local (needs ffmpeg for OGG) | Asynchronous voice notes, not conversation. No spoken reply |
| TUI dictation | `tui/voice/recorder.py`: Ctrl+R push-to-talk via `arecord`/`ffmpeg`, 16 kHz WAV → STT → compose box | via `SttSelector` | Local | Push-to-talk dictation only |
| TTS substrate | `media/tts/{piper,cloud,selector}.py` + agent tool `tools/media/tts.py` | **Piper** (`piper-tts`, auto-`pip install`ed on first use; voice `en_US-lessac-medium` from Hugging Face). Opt-in `CloudTtsBackend` for any OpenAI-compatible `/audio/speech` URL (off by default) | Local by default; cloud opt-in with egress disclosed | Writes a **WAV file and returns a path** (for `send_file`). No streaming, and replies are never spoken automatically (no `send_voice` anywhere). **Piper is now GPL-3.0** |
| Streaming hooks | `pipeline/streaming.py`: `ResponseChunk.kind = "answer" \| "progress"` | — | — | Good hook for spoken acknowledgements and progress narration |
| Web surface | `control_plane/server.py`: aiohttp app, GET JSON routes plus login, origin check, loopback detection | — | — | No WebSocket or WebRTC endpoint, no audio route, no voice session |

**Net:** the *local-first selector pattern* and *progress chunks* are reusable. The engines (batch PyTorch Whisper, file-based Piper) are not suitable for real-time conversation.

---

## Candidate stacks: comparison

All stacks keep StackOwl's pipeline as the brain. "Latency" means end of user speech → first bot audio, for a turn with **no tools**, on LAN.

| # | Components | Hardware tier | Expected latency | Licences | Risks |
|---|---|---|---|---|---|
| **A. "GPU cascade"** (recommended default for NVIDIA hosts) | Silero VAD + Smart Turn v3 → **Nemotron Speech Streaming EN 0.6B** (alt: Parakeet TDT 0.6B v2 behind VAD) → StackOwl pipeline → **Kokoro-82M** (alt: Chatterbox Turbo / Qwen3-TTS-0.6B for expressive or cloned voice); Pipecat + SmallWebRTC | NVIDIA desktop GPU, ≥12–16 GB for speech models only (LLM remote or on a second GPU) | 0.6–1.2 s (508 ms P50 measured with a local 30B LLM on an RTX 5090) **[M]** | MIT, BSD-2, NVIDIA Open Model License (not OSI) or CC-BY-4.0, Apache-2.0 | NVIDIA-only; NeMo dependency weight; NVIDIA licence vs "open source only" |
| **B. "Apple Silicon cascade"** | Silero + Smart Turn v3 → **MLX Whisper large-v3-turbo** or Parakeet-MLX → StackOwl → **Kokoro (MLX)**; Pipecat + SmallWebRTC | M-series Mac, ≥16 GB | 0.8–1.5 s (<800 ms measured with a local 4B LLM) **[M]** | MIT, CC-BY-4.0, Apache-2.0 | Community MLX ports; Whisper hallucination on noise |
| **C. "Portable CPU cascade"** | Silero + Smart Turn v3 → **Moonshine v2 streaming** or sherpa-onnx streaming model → StackOwl (remote LLM) → **Pocket TTS** (alt: Kokoro-ONNX, NeuTTS Air Q4) | Any x86/ARM CPU, Windows/Linux/Mac without GPU | 1.5–4 s **[L]** | MIT, Apache-2.0, CC-BY-4.0 (gated) | Conflicting CPU TTS numbers (200 ms vendor vs 1.7 s third party); lower STT accuracy |
| **D. "Jetson Orin edge"** | Silero + Smart Turn v3 → **whisper.cpp CUDA** / WhisperTRT (or sherpa-onnx Parakeet) → StackOwl with **remote** LLM → **Kokoro** (jetson-containers) or Pocket TTS on CPU cores | Jetson Orin Nano/NX/AGX (a *separate* test unit, not the dev box) | 1.5–3 s+ **[L]** | MIT, Apache-2.0 | CTranslate2 needs a source build; Riva on Orin lacks Magpie; memory contention with other services; few published numbers |
| **E. "Browser-edge hybrid"** | STT in browser (sherpa-onnx WASM + Moonshine) and/or TTS in browser (Kokoro WebGPU / Pocket TTS WASM); server only runs the pipeline; WebSocket carries **text** | Client-heavy: modern laptop or phone with WebGPU; any server | Unknown; phone-dependent **[L]** | MIT, Apache-2.0 | Phone thermals and battery; iOS WebGPU/WASM performance; model download size; AEC still client-side |
| **F. "Full-duplex research track"** (not recommended now) | **PersonaPlex-7B** or Moshi as a conversational front end, with delegation to StackOwl via a hybrid orchestrator | ≥24 GB NVIDIA GPU (CPU offload exists but is slow) | 160–205 ms model-level **[M]** | MIT code + NVIDIA Open Model License / CC-BY-4.0 | No tool calling; persona, not StackOwl's owls; English only; a second brain that can contradict the pipeline |
| **Rejected** | Web Speech API (cloud by default); XTTS-v2 (CPML non-commercial); F5-TTS weights (NC); LiveKit turn detector outside LiveKit Agents; openWakeWord stock models (NC-SA) for commercial redistribution | — | — | — | Licence or self-hosting violations |

**Capability-probe ladder (proposal):** CUDA GPU with ≥12 GB free → A. Apple Silicon → B. Else C (or D on Jetson). If the tier's measured budget is missed at runtime → degrade to *push-to-talk with spoken reply*, and surface the reason. This is consistent with the existing `select_local_first` / negative-cache pattern.

---

## Spikes we should run before committing

Run these on proper hosts: an RTX 4090/5090-class Linux box, an M-series Mac with 16–32 GB, a CPU-only x86 laptop, and a *separate* Jetson Orin unit. **Not the Jetson dev box.** Record every result in a latency CSV with P50/P95.

| # | Spike | Method | Pass | Fail |
|---|---|---|---|---|
| **S1** | **End-to-end latency harness** | Pipecat + SmallWebRTC per tier, with a stub LLM (fixed 200 ms TTFT) and then the real StackOwl provider. Play 50 recorded utterances into a browser via a fake mic; timestamp end of speech (VAD) → first audio sample at the browser | Tier A/B: **P50 ≤ 800 ms, P95 ≤ 1.5 s** (stub LLM). Tier C/D: P50 ≤ 2.5 s | Tier A/B P50 > 1.2 s → re-examine turn detection and TTS streaming before anything else |
| **S2** | **StackOwl turn latency** (likely the biggest risk) | Drive the real pipeline, as with `scripts/dev_ingress.py`, with 30 voice-style prompts: 10 chit-chat, 10 lookups, 10 tool-using. Measure time to first `answer` token and first `progress` chunk | Chit-chat first answer token ≤ 700 ms. Tool turns emit a `progress` chunk ≤ 1 s | First speakable output > 2 s for chit-chat → design a fast acknowledgement path (without disabling thinking) before building voice |
| **S3** | **STT bake-off on the owner's voice** | 100 owner-recorded phrases (commands, owl names, "StackOwl", tech jargon, far-field phone and laptop mics). Nemotron streaming (160/560 ms), Parakeet v2, Whisper-turbo (MLX/faster-whisper), Voxtral Realtime @480 ms, Moonshine v2 | WER ≤ 8% (GPU tiers), ≤ 12% (CPU tier); partial transcript ≤ 300 ms (streaming models); zero hallucinated text on 20 silence/noise clips | Any tier above its WER bound → that engine is dropped from the tier |
| **S4** | **TTS naturalness + time to first audio** | Kokoro, Chatterbox Turbo, Qwen3-TTS-0.6B, Pocket TTS, Kyutai TTS 1.6B, Piper. Fed sentence-by-sentence from a streaming LLM. Blind A/B by the owner on 20 responses | TTFA ≤ 250 ms (GPU), ≤ 600 ms (Mac), ≤ 1 s (CPU); the owner ranks a permissive-licence engine in his top two | No permissively licensed engine meets both → revisit licence policy (Decision D3) |
| **S5** | **Barge-in and echo on real devices** | iPhone Safari (speakerphone), Android Chrome, desktop Chrome on laptop speakers. Compare WebRTC remote-track playback vs WebSocket + Web Audio playback. Talk over the bot 30 times; let it speak 10 minutes with no user speech | Bot audio stops ≤ 300 ms after true barge-in (≥ 90%); **≤ 1 false barge-in per 10 min** of bot speech; no earpiece routing on iOS | Self-interruptions > 1 per 10 min → mandate WebRTC and/or a headset-only mode |
| **S6** | **End-of-turn quality** | 50 hesitant utterances with mid-sentence pauses (400–1,200 ms). Silence timeout (600 ms) vs Smart Turn v3 vs Kyutai semantic VAD | Premature cut-off ≤ 5%; mean end-of-turn delay ≤ 400 ms | Cut-offs > 10% → a longer timeout, with its latency cost accepted |
| **S7** | **iOS/Android session mechanics** | Installed PWA over HTTPS: permission persistence across launches, 10-minute session, lock screen, app switch, Bluetooth headset | 10-minute session with no re-prompt; clean, *announced* pause on background and resume on return | Permission re-prompts every session → evaluate a thin native wrapper (out of scope for the web dashboard) |
| **S8** | **Capability-probe ladder** | Run the probe on all four hosts, then force-degrade (kill the GPU process, throttle the CPU) | Correct tier chosen on each host; each tier meets its S1 bound, or degrades to push-to-talk with a surfaced reason | Wrong tier picked, or a silent failure |
| **S9** | **Cancellation semantics** | Barge-in during (a) TTS only, (b) LLM streaming, (c) a running tool call | Behaviour matches the owner's decision (D9) in all three; no orphaned audio; no duplicate task | Any divergence |
| **S10** | *(optional)* **Wake word** | Train a custom openWakeWord "Hey Owl" model; run in-browser (onnxruntime-web) on a desktop tab for 8 h of ambient audio plus 100 deliberate triggers | ≤ 1 false accept per 8 h; ≤ 10% false rejects; < 5% CPU | Worse → push-to-talk / tap-to-talk only |
| **S11** | *(optional)* **Full-duplex feel** | PersonaPlex-7B on a 24 GB+ GPU vs Stack A, blind by the owner, 10 conversations | Owner strongly prefers PersonaPlex *and* a delegation prototype keeps tool results correct | Otherwise park the speech-to-speech track for six months |

---

## Decisions the owner must make

**D1. Conversation architecture.**
- (a) Streaming cascade around the existing pipeline. *Recommended.*
- (b) Native full-duplex speech-to-speech (PersonaPlex/Moshi) as the brain.
- (c) Hybrid: a full-duplex talker for backchannels plus the pipeline for substance (research).

**D2. Where speech compute runs.**
- (a) Always on the StackOwl server, with a capability-probed tier ladder.
- (b) Browser-edge where the client is capable (Stack E), server otherwise.
- (c) A dedicated "voice node" machine separate from the core (e.g. the GPU box serves voice for a Jetson or CPU core).

**D3. Licence policy for speech models.** This decides whether Stack A's primary STT and today's Piper survive.
- (a) OSI-permissive only (MIT/Apache/BSD).
- (b) Also CC-BY-4.0, with attribution (Parakeet, Kyutai, Pocket TTS).
- (c) Also custom commercial-OK model licences (NVIDIA Open Model License, the Llama licence behind Orpheus).
- (d) Also GPL-3.0 (Piper; affects distribution of anything that links it).

**D4. How a conversation starts.**
- (a) Push-and-hold.
- (b) Tap to start a session, then open mic with VAD, turn detection and barge-in. *Recommended for mobile.*
- (c) Wake word on a foreground desktop tab (needs a self-trained model; S10).
- (d) Always-listening. Not feasible in mobile browsers.

**D5. Transport.**
- (a) WebRTC via Pipecat `SmallWebRTCTransport` (plus coturn TURN for remote access).
- (b) Own WebRTC (aiortc) inside the aiohttp control plane.
- (c) WebSocket PCM on the existing control plane: simplest, but worse on mobile networks and no Chromium AEC for Web Audio playback.
- (d) Self-hosted LiveKit server (an extra Go service; only if multi-party or SIP is wanted).

**D6. Framework: port or build** (per the port-before-build rule).
- (a) Adopt Pipecat as the real-time voice loop inside a StackOwl "voice channel" adapter. The pipeline stays the only brain.
- (b) Build the loop in-house on the existing selector pattern.
- (c) Adopt Pipecat for a prototype only, then decide.

**D7. The voice's identity.**
- (a) A stock voice (Kokoro/Pocket TTS voice list).
- (b) A designed voice (Qwen3-TTS voice design from a text description).
- (c) A clone of a *consenting* person (the owner, or a hired voice actor). Never a film character's actor.
- (d) Per-owl voices (each owl gets its own voice).

**D8. Silence during long agentic turns.**
- (a) An immediate spoken acknowledgement, then spoken `progress` chunks, then the answer.
- (b) Earcons or "thinking" sounds only.
- (c) Silence with on-screen progress.
- (d) A fast small model speaks a holding reply while the main model thinks (hybrid routing, already on the backlog).

Not an option: disabling thinking.

**D9. What barge-in cancels.**
- (a) Speech only; the task keeps running and its result is summarised later.
- (b) Speech and LLM generation, but not tool calls already in flight.
- (c) Everything, including in-flight tools where they can be cancelled.

This interacts with the authority-vs-action consent model.

**D10. Spoken consent.** Can "yes" by voice approve a consent-gated action?
- (a) Never; consent always needs an on-screen tap.
- (b) Yes for low-severity actions, a tap for high-severity ones.
- (c) Yes, with speaker verification (adds a model and risk: TTS echo, bystanders, recordings).

**D11. Transcript handling.**
- (a) Auto-send each detected turn, with a live transcript on screen. *Recommended for conversation.*
- (b) Keep Telegram-style confirm-before-send, which breaks conversational flow.
- (c) Per-channel: live conversation auto-sends, voice notes confirm.

**D12. Minimum hardware floor for "conversation mode".** Below what tier does the dashboard offer only push-to-talk dictation plus a spoken reply?
- (a) No floor; always offer, and warn about latency.
- (b) Floor = Tier B-equivalent measured in S8.
- (c) Floor decided per host from S1 results.

**D13. Remote access.** Is voice LAN-only at first, or reachable from outside (phone on mobile data)? Outside needs HTTPS with a real certificate plus a self-hosted TURN server (coturn) for WebRTC.

**D14. The existing Piper dependency.** Given the GPL-3.0 change:
- (a) Keep it (accept GPL).
- (b) Replace the local default with Kokoro (Apache-2.0) or Pocket TTS (CC-BY-4.0).
- (c) Pin to the archived MIT `rhasspy/piper` release. Unmaintained.
