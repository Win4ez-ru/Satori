# ADR-0081: Local Neural Voice and Read-only Expression Delivery

- Status: Accepted for implementation; acoustic and human acceptance remain separate
- Date: 2026-10-19
- Authority: explicit user rejection of D2.1 recognition/system voice, request for emotional
  delivery, and subsequent approval of cloud synthesis if its voice is noticeably better
- Supersedes: ADR-0080 recognizer and default speech-engine choices only
- Preserves: ADR-0080 capture, draft, Send, commit, privacy, cancellation and release boundaries

## Decision

Replace the local recognizer with Russian GigaAM v3 E2E RNNT int8 through ONNX-ASR. The pinned
resources are the third-party `istupakov/gigaam-v3-onnx` export at revision
`322c3b29492673eb7d0b434bfa9dfb8653e34d02`, six files /226,431,997 bytes. The export publisher
is distinguished from upstream Salute GigaAM. Use CPU execution and NumPy preprocessing with
the actual capture sample rate, including 44.1/48 kHz. No language-model correction, ASR prompt,
automatic Send or new canonical evidence path is added.

Evaluate and integrate Qwen3-TTS 12Hz 1.7B CustomVoice 4-bit through MLX-Audio on Apple Silicon.
The fixed public resource is `mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-4bit` at revision
`f35faf19b0cc2160865af64ecf0f22f83d335135`, fourteen files /2,312,059,416 bytes. Exact size/hash
pins are owned by the neural-model adapter. Keep the same preset speaker (`Serena`) across
delivery changes. This is a multilingual candidate, not a claim of native Russian or accepted
naturalness. The 0.6B candidate is rejected: it ignores delivery instructions and the initial
audition contained omissions/degeneration. Preserve those failed samples and measurements.

Initial Ono_Anna trials and an exact-reading/lower-temperature ablation were rejected for content
insertions, omissions or degeneration. Serena's final actual-production instruction corpus has
four exact style comparisons and preserved held-out negation/content; the proper name in its
number case remains a disagreement between two ASR proxies, requiring human audition. Keep
literal scores separate from number formatting and this uncertainty.

The user subsequently auditioned and rejected Serena for unnatural stress, pacing and rhythm.
This is a failed human-quality gate even though the short content proxies improved. Keep local
neural speech explicitly experimental, never the recommended/default voice, and do not install
its 2.31 GB weights into the normal application model directory as part of this delivery.
Missing/invalid resources or inference failure
leave the committed text available and report a closed error code; never silently fall back to
the rejected system voice or send replies to a cloud service. Old system voice/rate preferences
remain readable for compatibility and an explicit legacy route; WPM settings do not masquerade
as a working neural speed control. Existing public models remain separate from the app and
canonical profile. Preparation is explicit, TLS-verified, bounded, pinned and atomic/no-replace.
Startup checks are read-only and perform no download, inference, recording or autoplay.

## Text and expression projections

Canonical reply text remains unchanged. A deterministic outgoing speech copy removes decorative
emoji clusters and Markdown presentation while retaining meaningful text, link labels, operators
and literal code. No paraphrase, generated replacement or new state write occurs. Emoji-only
replies start no speech. Unicode/markup tests establish rendering behavior, not acoustic quality.

Audio delivery reads only the immutable manifest attached to this exact newly committed reply.
For current policy >=24, the authoritative inputs are existing `CharacterDeliveryDecision` and
`CharacterPresenceProjection` fields: selected delivery voice/goal/pressure, affect profile and
closed qualitative affect signals. Legacy expression-plan fields are prohibited by the current
Core contract and are not consulted. Do not inspect reply words to invent an emotion, re-read
mutable affect after commit, rerun appraisal, change Character prompts or create another mood,
emotion, personality or relationship owner.

A frozen desktop `VoiceDelivery` contains only validated closed source codes. Its English
engine instruction is assembled locally from fixed, bounded prosody templates. Primary delivery
follows the already selected voice; secondary affect can modestly add engagement, brightness,
contained strain or reflective quiet. Positive affect alone does not invent a joke. Tension is
non-hostile; reserve/subdued mood does not prove the discrete emotion "offended". Unknown,
legacy or inconsistent metadata yields neutral delivery. No arbitrary instruction field, raw
state numbers, user text, retrieved material, self prompt or free-form explanation enters this
instruction channel. The supplied reply text is the content channel, with fixed Russian language.

Text and delivery travel in the same correlated successful send result. Existing request,
profile/session, replay and interaction-deduplication fences apply to both. History, status,
startup, replay, error or late responses cannot initiate speech. The projection is ephemeral;
it is neither canonical persistence nor new self/relationship evidence. Character remains paused.

## Process and playback boundary

One owned child verifies all model hashes, lazily loads local safetensors, denies network
connections and synthesizes PCM through a private bounded JSONL pipe. Normal stdout/stderr remain
discarded for the child's lifetime; no vendor exception, reply, audio body or prompt enters
diagnostic logs. PCM16LE mono records have a sequence, fixed negotiated sample rate and a maximum
one-second payload. The writer coalesces vendor/HTTP fragments into 250 ms records plus a final
residual, bounding record count independently of arbitrary transport fragmentation. A complete
record must match total bytes and a successful child exit.
Stateful resampling preserves continuity across generated chunks. Synthesis/playback are bounded
to 120 seconds of audio and a separate wall timeout; truncation is an error, not a complete reply.

Qt playback opens the native output only on nonempty PCM, handles partial writes and reports
completion only after both queued and native audio drain. Stop/new capture/scope change/Quit
immediately reset the owned sink, discard pending PCM and terminate the owned inference child.
Generation fences prevent late chunks from restarting it. Setup uses graceful termination with
a bounded hard-kill fallback; only its own incomplete staging may be removed. Abrupt death may
leave an unpublished staging directory and is not claimed as physical erasure.

Streaming here means incremental audio for an already committed reply. It does not stream an
uncommitted LLM answer, add simultaneous listening, hands-free operation, VAD, automatic turn
admission or realtime/barge-in. D3, Stage 15 and full historical Stage 20 remain closed.

## Explicit cloud option authorized later in this task

The user subsequently permitted cloud synthesis when voice quality is noticeably better. Add
an explicitly selected ElevenLabs route alongside the local route; never infer that authorization
for every reply before the user selects this backend. Use one fixed TLS origin and REST streaming
endpoint, `eleven_v4`, enforced Russian, and raw 24 kHz mono PCM. No microphone audio, user message,
history, retrieved context, Core prompt, numeric affect state or identity document is disclosed.
Only the rendered committed answer (or a deliberately clicked public preview) and closed audio
direction tags leave the computer. Provider retention applies; do not assert zero retention.
The provider's `enable_logging=false` option is enterprise-only and is not silently requested.

The candidate voice is Russian female Marina (`ymDCYd8puC7gYjxIamPt`), identified from the public
provider page and described as soft, clear and warm. This verifies selection metadata, not the
user account's access, a price, live quality or emotional acceptance. Voice ID is editable and
can be saved. ElevenLabs becomes the preferred setup backend after this explicit user decision;
automatic speech remains off. Migrating legacy preferences without a backend resets their old
system-only autoplay choice to off, so it cannot authorize paid cloud requests. A key and an
explicit preview/opt-in are still required; absence of a key is not a passing live integration.
Cloud setup visibly explains text disclosure,
provider retention and possible account charges. Do not promise that every library voice is
available on a free plan.

An API key is entered through a bounded password field in the application, retained in this
application process's memory only, and passed to the owned child through private stdin. It is
never a preference field, environment variable, command argument, diagnostic or artifact. An
empty field preserves an already entered session key; an explicit forget action clears it.
Restart requires re-entry. This bounded session-only option does not claim Keychain integration
or forensic memory erasure. Existing Core provider credentials are not reused or searched for.

The cloud child validates credentials/voice ID before connecting, uses certificate verification,
and supports no ambient proxies, cookies, redirects, alternate origins or automatic retries.
Bound inactivity, total request wall time, PCM framing/duration and response bytes. Reject
non-audio/encoded responses and expose only closed credential/quota/network/provider error
codes. An uncertain/cancelled paid request is never automatically repeated or refunded by the
adapter. Stop closes only the owned process and resets audio independently of Core.

A manual preview uses one fixed authored Russian sentence and neutral/playful/reserved delivery
choices. It never becomes a conversation, memory or emotion event and cannot run on startup.
Automated mocked transport tests and invalid frozen-worker requests establish adapter behavior,
not provider/account/voice acceptance. A real credential and actual audition are necessary for
cloud quality verification; absent credentials leave that boundary explicitly unverified.

Provider references: [stream speech](https://elevenlabs.io/docs/api-reference/text-to-speech/stream),
[audio tags](https://elevenlabs.io/docs/help-center/product/core-capabilities/text-to-speech/how-do-audio-tags-work-with-eleven-v3-and-v4),
[Russian voices](https://elevenlabs.io/text-to-speech/russian).

## Verification

Require strict protocol/text/expression tests, fake native PCM partial-write/drain/reset checks,
real owned-worker cancellation/stale-callback tests and exact pinned nested-model validation.
Compare the same authored Russian recordings with the previous recognizer, then held-out words,
negation, noise and sample rates. Keep model load, decode, first PCM, playback and Core timings
separate. ASR retranscription is a content proxy, not human TTS quality acceptance.

Audition the same text/speaker with different delivery instructions, plus held-out content.
Style routing alone does not establish audible or appropriate emotion. Reject omissions,
negation changes and degeneration; do not hide them with phrase-specific tuning or rewriting.
Verify the actual frozen app's MLX Metal library, ONNX preprocessor resources, worker routes,
signature and resource boundaries before installation. Rebuild the noneditable Core package,
freeze source/tests/dependencies and final app, then run the complete Foundation gate. Preserve
both prior app versions and failed evidence. Record current engineering, natural microphone,
audibility/emotion, human and distribution acceptance separately in `docs/progress.md`.

Primary implementation references: [GigaAM](https://github.com/salute-developers/GigaAM),
[ONNX-ASR](https://github.com/istupakov/onnx-asr),
[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS),
[MLX-Audio](https://github.com/Blaizzy/mlx-audio). Their code, dependencies, model exports and
resource licenses require separate attribution/release review; no universal license is asserted.
