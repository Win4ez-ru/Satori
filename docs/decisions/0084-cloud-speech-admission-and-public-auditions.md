# ADR-0084: Cloud speech admission and explicit public emotion auditions

- Status: Accepted for bounded implementation; voice quality remains unaccepted
- Date: 2026-10-20
- Authority: the user's emotional-voice request and explicit coordination/integration continuation
- Supersedes: ADR-0081's unconditional literal-code preservation for cloud transmission only
- Preserves: canonical text, existing read-only delivery metadata, explicit cloud choice,
  session-only credentials, paid-call disclosure, separate Send and all cancellation boundaries

## Decision

Provider text shares the audio-direction channel with ElevenLabs tags. Canonical reply text
must not introduce arbitrary provider directions merely by containing square-bracket tags.
Admit the deterministic rendered outgoing copy before any cloud network operation. Refuse
residual square brackets (including equivalent compatibility forms), literal decorative emoji
and explicitly enumerated unsupported presentation residue with a closed text-only status.
Do not silently delete literal code, negations or operators to make a reply admissible.
The unchanged canonical reply remains visible. No automatic retry or fallback occurs.

Only after admission, prefix the existing closed delivery tags attached to this exact committed
reply. The guard is a conservative syntactic boundary, not proof of semantic prompt-injection
immunity or acoustic behavior. Local speech compatibility remains explicit; source content must
never mutate Character, emotion, mood or relationship state.

Add an immutable catalogue of authored public audition phrases: neutral, curious, subtle humor
and reserved delivery, with the same speaker and clear audition labels. Use the existing manual
preview button and bounded owned speech process. No free-text TTS instruction, automatic call,
new account creation, voice saving, model change or Voice Design generation belongs here.
Show that each preview sends its public phrase to the selected provider and can use paid quota.
Preserve saved Voice IDs and autoplay choices. Replace misleading fixed-timbre UI help with
accurate Voice-ID guidance; do not claim an unheard designed voice is accepted.

## Verification

Cover control tags in prose/code/escaped/unclosed brackets, compatibility forms, grapheme emoji,
flags/keycaps/variation selectors, ordinary link labels, negations and mathematical operators.
Assert rejected content creates no HTTP connection or paid request. Test immutable public cases,
closed delivery mapping, real VoicePanel preview dispatch, and cancellation/profile/editor fences.
Follow the coordinated Foundation/bundle gate; preserve failed human auditions separately.

Human timbre, stress, pace, same-speaker emotional range and authenticated API quality remain
separate checks. A designed preview ID is not a saved runtime voice. No credential or paid
generation is exercised automatically by this implementation decision.
