# ADR-0080: Local Push-to-talk and Committed Reply Speech

- Status: Accepted for the bounded D2.1 implementation below
- Date: 2026-10-18
- Authority: the user's explicit continuation after the D1 desktop delivery
- Extends: ADR-0079; preserves ADR-0012 and ADR-0046 canonical input/evidence boundaries

## Decision

Open D2.1 as a local desktop voice adapter: deliberate microphone capture → local Russian
transcription → visible editable draft → separate explicit Send → existing Core commit → optional
local reply speech. Character improvement remains paused. No Core owner, schema, prompt,
provider policy, canonical conversation contract or existing CLI route changes.

The microphone is off at startup. A held push-to-talk button is the only capture gesture.
Permission is requested on that gesture, never on opening the app. Granting an OS permission does
not start capture: the user presses again. Capture is visible and bounded to 30 seconds /3 MB
of mono PCM16. Release transcribes; cancellation, lost focus/window hide, permission loss,
timeout, profile/session changes and runtime shutdown discard capture and invalidate callbacks.
There is no always-listening, wake phrase, automatic end-of-utterance or cloud speech route.

Audio exists only in bounded process memory and inherited pipes. No temporary audio file,
recording history, transcript/response body in diagnostic logs or automatic audio retention is
introduced. Python/Qt/OS buffers are not a claim of forensic erasure or completed local privacy.

Use replaceable desktop-local ports. Initial STT is faster-whisper small multilingual, CPU int8,
in a separate cancelable owned process. This avoids blocking Core commands or sharing Ollama GPU
inference. Fixed model resources use a pinned public revision, sizes and SHA-256 checks. Model
download requires an explicit setup action; opening the application performs no download or
inference. No private audio, prompt, transcript or account credential is sent during setup.

The fixed recognizer resources are `Systran/faster-whisper-small` at revision
`536b0662742c02347bc0e980a01041f333bce120`, approximately 486 MB outside the application/profile.
The [official immutable repository](https://huggingface.co/Systran/faster-whisper-small/tree/536b0662742c02347bc0e980a01041f333bce120)
is the resource source; exact file sizes and SHA-256 pins belong to the model-setup adapter.
An automatic owned `--check-only` worker may read and validate existing files at startup; it never
downloads, creates directories or runs recognition. Capture requires successful verification.
The GUI caches only a file-metadata fingerprint for cheap change detection; the recognition
worker verifies hashes again. Explicit download uses fixed HTTPS origins, verified certificates,
bounded streaming and atomic no-replace publication; incomplete/unrelated destinations remain
untouched. Readiness is not an additional persistent domain state.

Recognized text is uncertain. Language/engine and unknown confidence are presented honestly;
an ASR score is not a calibrated probability of correctness. The visible text becomes canonical
user input only after the user reviews/edits it and presses the existing Send button. The same
application path and owner commit apply as for typed text. Raw audio and unreviewed ASR hypotheses
are not new memory/self/relationship evidence. This slice does not claim durable audio provenance
or completion of the full historical Stage 20.

Voice operations are independent of the serial Core worker. Every callback is fenced by operation
generation, profile/session and editor revision. A late result cannot overwrite a newer draft or
another conversation. Cancel never silently submits text. Failed speech leaves text chat available.

Initial macOS TTS uses the installed system voice through owned `/usr/bin/say`, with text on stdin
and validated voice/rate settings. The initial candidate is `Milena` at 165 words/minute, a calm
adult female direction selected for the user's request that the voice fit the character. This is
a configuration choice, not human audition/voice acceptance or a change to Character state.
Automatic speech is opt-in. Only a new correlated successful
send result can start it, once per committed interaction; history, startup, errors and replay remain
silent. Stopping speech stops delivery, not the committed interaction. New capture, scope changes
and Quit stop speech immediately, independently of a busy Core. Other TTS/cloud engines and
emotional prosody remain future adapters; no additional affect owner is created.

## Verification and release boundaries

Implementation and engineering verification are in progress as of 2026-10-18. Actual evidence,
including separately pending physical and human checks, belongs in the
D2.1 record and `../progress.md`.

Require capture/permission/cancel/timeout/stale-callback tests, bounded malformed/silent audio,
fixed-model integrity and no-overwrite download checks, explicit Send/no admission from ASR,
committed-only playback and stop independent of Core. Package QtMultimedia, microphone purpose
text and actual runtime dependencies explicitly. Keep models, user profiles and recordings outside
the app. Rebuild the wheel before the frozen Foundation gate; preserve the D1 app as rollback.

Record real local Russian recognition and engine timings separately from a microphone hardware
test, actual speaker audibility, female voice quality and human acceptance. Synthetic audio or an
owned-process test does not establish microphone permission/revocation, natural speech quality,
hands-free behavior or a clean-machine release. Public distribution additionally needs actual
binary-license notices, signing/notarization and the existing privacy/release gates. Dependencies
and the model are not described as having one universal license.

D2.1 does not open D3 tools, personal connectors, autonomous tasks, Stage 15 or Character changes.
