"""Local-only AI remark rewriting for IND3 V3.1 Shift Happenings.

Adapted from V3's ai_rewriter.py (reference repo ind3-v3-reference,
ai_rewriter.py). V3's own file is dormant by design (_USE_AI = False) --
Isaac's explicit call there was "deterministic cleanup only, no AI
paraphrasing" for *production report generation*, because that pipeline
would have silently substituted the AI's output with no human review.
This module is intentionally different: it is a SEPARATE, opt-in
suggestion a human must explicitly review and accept via the WO edit
modal (see v31_reporting.request_ai_rewrite/accept_ai_rewrite/
reject_ai_rewrite). Because a person is always in the loop here, V3's
determinism objection does not apply the same way -- but the content-
safety guards that objection produced (never invent a part, a diagnosis,
a resolution; never drop a WO number or asset ID) absolutely still
apply, and are ported over nearly verbatim below.

This does NOT replace spelling_fix.py. The deterministic spelling
correction from Phase 1 still runs on every remark unconditionally; this
module only ever receives already-spelling-corrected text as its input
and offers a further, optional, human-reviewed rewrite on top of it.

Hard privacy rules (enforced in code, not just by default config):
  * Only ever talks to a local Ollama instance at 127.0.0.1/localhost.
    Any other OLLAMA_BASE_URL is rejected outright at import time --
    see _is_local_url(). There is no code path that calls any other host.
  * No cloud fallback of any kind. If the local engine is unavailable,
    callers get a clear, structured "unavailable" result -- never a
    silent switch to a remote provider, never a silent guess dressed up
    as a real answer.
  * Remark/asset/WO text is never written to logs at INFO level or above
    (see log calls below -- only lengths/booleans are logged, never the
    text itself).

Public API
----------
check_ai_available() -> dict
    {"available": bool, "model": str | None, "reason": str | None}
suggest_rewrite(remark, asset_id="", wo_number="") -> dict
    {"ok": bool, "text": str | None, "reason": str | None}
    Never raises. ok=False always carries a human-readable `reason` so
    the MST is told why, never just silently given nothing.
"""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

# -- Configuration -----------------------------------------------------------
# Env-overridable with safe local-only defaults, matching the pattern
# already used for IND3_V31_PRIVATE_DIR / IND3_V31_ROSTER_PATH elsewhere in
# this codebase.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")  # "" = auto-detect first installed model
_TIMEOUT = float(os.environ.get("IND3_V31_AI_TIMEOUT", "90"))
_TEMPERATURE = 0.1
_MIN_WORDS_FOR_AI = 8  # below this, a small local model reliably pads with fabricated detail

# Test-only escape hatch: a subprocess-based real-browser test cannot
# monkeypatch this module's HTTP call directly (it runs in a different OS
# process), so a deterministic, clearly-labeled mock path is offered
# instead. Never used outside of tests, and never silently selected -- it
# requires this exact env var to be set, which test fixtures do explicitly.
_MOCK_MODE = os.environ.get("IND3_V31_AI_MOCK") == "1"
_MOCK_RESPONSE = os.environ.get("IND3_V31_AI_MOCK_RESPONSE", "")

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _is_local_url(url: str) -> bool:
    """True only for a loopback address. Rejects every public/cloud host --
    the one hard, non-configurable line in this whole module."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return host.lower() in _LOCAL_HOSTS


if not _MOCK_MODE and not _is_local_url(OLLAMA_BASE_URL):
    raise RuntimeError(
        f"OLLAMA_BASE_URL={OLLAMA_BASE_URL!r} is not a local address. "
        "AI remark rewriting must only ever talk to a local Ollama "
        "instance (127.0.0.1/localhost) -- refusing to start rather than "
        "risk sending Walmart equipment/work-order/technician data to an "
        "external service."
    )


# -- System prompt -------------------------------------------------------------
# Ported near-verbatim from V3's ai_rewriter.py (Core Rules 1-36), proven
# over real production remark data. Trimmed: the asset-type-glossary/
# ride-on-vehicle rules (9a's glossary enforcement, V3's _ASSET_TYPE_GLOSSARY)
# are dropped -- V3.1 has no equivalent per-asset-type catalog, and
# guessing at one would be speculative (YAGNI). The general principle of
# rule 9a (never guess equipment type) is kept as plain prompt text.
_SYSTEM_PROMPT = """
Purpose
Rewrite technician remarks into concise, professional industrial maintenance
passdown entries suitable for engineering shift handoffs and CMMS history.
Improve readability only while preserving every technical detail exactly as
documented.

Core Rules
1. Preserve every technical fact, finding, troubleshooting step, corrective
   action, verification step, recommendation, escalation, and outcome
   exactly as described.
2. Never invent, assume, infer, exaggerate, simplify, summarize, or remove
   information.
3. Improve grammar, spelling, punctuation, and readability only.
4. Preserve the original chronological sequence of events.
5. Fix grammar, spelling, word order, and punctuation only. Do NOT replace
   the technician's own words with different vocabulary or more
   "professional"-sounding phrasing. If they wrote "brush", output "brush".
   The goal is the technician's own words, lightly cleaned up -- not a
   rewritten version in different vocabulary.

Technician Language
6. Remove all first-person language.
   I replaced -> Replaced | We adjusted -> Adjusted | I noticed -> Found
6a. Never invent a subject noun to replace the removed pronoun. Do NOT
    write "Technician replaced" or "MST inspected" -- go straight to the
    action verb with no subject at all.
7. When a remark begins with "Assisted with"/"Assisting with", remove the
   participation wording and rewrite only the completed maintenance action.
7a. If the entire remark is only a vague participation phrase with no
    specific asset, component, action, finding, or technical detail, output
    only a dash: -
8. Remove conversational filler: anyways, just, basically, had to, went
   ahead and, no worries.

Technical Terminology
9.  Preserve all asset names, component names, and technical terminology
    exactly as written.
9a. Never invent, assume, or guess the type or category of equipment being
    described. Only use an equipment-type word if it appears in the
    technician's remark. If not stated, refer to it only as "the asset" or
    by its asset ID -- never guess.
10. Preserve all directions exactly: Left, Right, Upper, Lower, Front,
    Rear, Top, Bottom.
11. Preserve all measurements, work order numbers, fault codes, locations,
    identifiers, quantities, and equipment references exactly.
12. Preserve technician maintenance vocabulary whenever technically
    correct (Unjammed, Swapped, Power cycled, Recalibrated, Realigned,
    Deinstalled, Lubricated, Tightened, Pulled).
13. Preserve maintenance acronyms exactly. Never expand: LOTO, VFD, PLC,
    HMI, PM, RTS, OOS, MST, WO.

Troubleshooting
14. Preserve the complete troubleshooting sequence in order: Finding ->
    Investigation -> Escalation (if applicable) -> Recommendation ->
    Diagnosis -> Corrective Action -> Testing -> Verification -> Final
    Status.
15. Preserve every observation separately from the corrective action
    whenever both exist.
16. Preserve the technician's troubleshooting narrative exactly. Do not
    rewrite it into an engineering report or shorten it.
17. Preserve root causes exactly. Never generalize or simplify technical
    findings.
18. Preserve all testing and verification steps exactly.
19. Preserve every escalation. If Technical Support, Engineering, a
    vendor, or maintenance leadership was contacted, this MUST appear in
    the output.
20. Preserve every recommendation received from support personnel.
21. Preserve cause-and-effect relationships. Never invent a cause, a next
    step, a part, or an escalation the technician did not state.

Operational Status
22. Only state operational status when explicitly supported by the
    technician's remarks.
23. Never invent operational status or diagnostic conclusions.
24. Normalize operational wording to: "Asset returned to service."
25. Never leave incomplete phrases such as "Asset returned to full."

Prohibited
26. Never generate filler phrases: "No further details provided",
    "Maintenance performed", "Operational status unknown", "Issue
    addressed", "Investigation continued", "Corrective action taken".
27. Never remove maintenance history that could help future
    troubleshooting.
28. Never replace technician terminology with polished report language.

Final Validation
Before returning the final output, verify every technical detail,
troubleshooting step, observation, root cause, escalation, and
recommendation has been preserved; no operational status has been
invented; and no outcome appears that wasn't stated in the original.

Output only the rewritten note. Nothing else.
"""

_SINGLE_TEMPLATE = (
    "Rewrite the following technician remark as a single, flowing "
    "professional engineering shift passdown entry. Follow all rules "
    "exactly. Output only the rewritten note. Do not include the asset ID "
    "anywhere in the output -- the report already shows it separately.\n"
    "---\n"
    "Original: {text}\n"
    "Passdown:"
)


# -- Guard/cleanup helpers -----------------------------------------------------
# Ported from V3's ai_rewriter.py. Model-agnostic text validation -- kept
# deliberately independent of which Ollama model is actually installed.

_INFORMAL = [
    (r"all systems go!?", ""),
    (r"back in business\.?", "asset returned to service."),
    (r"good to go!?", "asset returned to service."),
    (r"up and running", "returned to service"),
    (r"as usual\.?", ""),
    (r"no worries\.?", ""),
]

_ASSISTED_WITH_PATTERNS = [
    (r"(?i)\bAssist(?:ed|ing)? with adjusting\b", "Adjusted"),
    (r"(?i)\bAssist(?:ed|ing)? with replacing\b", "Replaced"),
    (r"(?i)\bAssist(?:ed|ing)? with installing\b", "Installed"),
    (r"(?i)\bAssist(?:ed|ing)? with calibrating\b", "Calibrated"),
    (r"(?i)\bAssist(?:ed|ing)? with removing\b", "Removed"),
    (r"(?i)\bAssist(?:ed|ing)? with\b", "Performed"),
    (r"(?i)\bAssisting\b", "Performing"),
]


def _normalize_assisted_with(text: str) -> str:
    for pattern, replacement in _ASSISTED_WITH_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text


_LEADING_VERB_PAST_TENSE = {
    "take": "Took", "taking": "Took", "perform": "Performed", "performing": "Performed",
    "check": "Checked", "checking": "Checked", "clean": "Cleaned", "cleaning": "Cleaned",
    "install": "Installed", "installing": "Installed", "replace": "Replaced",
    "replacing": "Replaced", "inspect": "Inspected", "inspecting": "Inspected",
    "test": "Tested", "testing": "Tested", "adjust": "Adjusted", "adjusting": "Adjusted",
    "calibrate": "Calibrated", "calibrating": "Calibrated", "verify": "Verified",
    "verifying": "Verified", "confirm": "Confirmed", "confirming": "Confirmed",
    "tighten": "Tightened", "tightening": "Tightened", "lubricate": "Lubricated",
    "lubricating": "Lubricated", "remove": "Removed", "removing": "Removed",
}
_LEADING_VERB_RE = re.compile(r"^([A-Za-z]+)\b")


def _fix_leading_tense(text: str) -> str:
    """Convert a present-tense/gerund verb at the START of each sentence to
    simple past. Mid-sentence verbs are left alone -- only the leading word
    of a sentence is a safe enough signal it describes a completed action."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    fixed = []
    for s in sentences:
        m = _LEADING_VERB_RE.match(s)
        if m:
            past = _LEADING_VERB_PAST_TENSE.get(m.group(1).lower())
            if past:
                s = past + s[m.end(1):]
        fixed.append(s)
    return " ".join(fixed)


_INVENTED_SUBJECT_PATTERN = re.compile(
    r"(?:^|(?<=[.!?]\s))(?:the\s+)?(?:technician|tech|mst|associate)\s+(\w+)",
    re.IGNORECASE,
)


def _strip_technician_subject(text: str) -> str:
    return _INVENTED_SUBJECT_PATTERN.sub(lambda m: m.group(1).capitalize(), text)


_FIRST_PERSON_WORD = re.compile(r"\b(?:i|we)(?:'m|'re|'ve|'d|'ll)?\b(?!-)\s*", re.IGNORECASE)


def _strip_first_person(text: str) -> str:
    if not text:
        return text
    text = _FIRST_PERSON_WORD.sub("", text)
    text = re.sub(r"\s+([,.;])", r"\1", text)
    text = re.sub(r"  +", " ", text).strip()
    if text:
        text = text[0].upper() + text[1:]
    text = re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    return text


def _strip_leading_asset_id(text: str, asset_id: str) -> str:
    """Safety net: remove the asset ID anywhere it slipped into the
    output -- the report already shows it separately."""
    if not asset_id:
        return text
    text = re.sub(rf"^{re.escape(asset_id)}\s*[-:/]?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(rf"\b{re.escape(asset_id)}\b", "the asset", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)\bthe\s+the asset\b", "the asset", text)
    return re.sub(r"  +", " ", text).strip()


_INVENTED_COMPONENT_LABELS = ["fill head", "photo eye", "motor", "actuator", "bearing", "roller"]


def _strip_invented_component_labels(output: str, original: str) -> str:
    """Remove a descriptive component label the model attached to a bare
    part ID, when the technician's own remark never used that label."""
    orig_low = original.lower()
    for label in _INVENTED_COMPONENT_LABELS:
        if label in orig_low:
            continue
        pattern = re.compile(
            rf"\b{re.escape(label)}s?\s*(?:component)?s?,?\s*(?=[A-Za-z]?\d{{2,5}}\b|\band\b)",
            re.IGNORECASE,
        )
        output = pattern.sub("", output)
    output = re.sub(r",\s+(were|was)\b", r" \1", output)
    return re.sub(r"  +", " ", output).strip()


_VAGUE_EQUIPMENT_PHRASE = re.compile(
    r"\b(?:an?\s+)?(?:unidentified|unspecified|unknown)\s+piece\s+of\s+(?:equipment|machinery)\b",
    re.IGNORECASE,
)


def _strip_vague_equipment_phrase(output: str) -> str:
    return _VAGUE_EQUIPMENT_PHRASE.sub("the asset", output)


_LEAKED_CONTEXT_LINE = re.compile(r"\s*Equipment context\b.*$", re.IGNORECASE | re.DOTALL)


def _strip_leaked_context_line(output: str) -> str:
    return _LEAKED_CONTEXT_LINE.sub("", output).strip()


def _clean_output(text: str) -> str:
    """Strip informal phrases and fix formatting the model sometimes adds."""
    void_phrases = re.compile(
        r"^(?:assist(?:ed|ing)?|help(?:ed|ing)?|support(?:ed|ing)?|perform(?:ed|ing)?)"
        r"\s+(?:with\s+)?(?:the\s+)?(?:repair|fix|work|maintenance|task|job|effort|project|team)\.?$",
        re.IGNORECASE,
    )
    if void_phrases.match(text.strip()):
        return "-"
    if text == text.upper() and len(text) > 20:
        text = text.capitalize()
    text = _normalize_assisted_with(text)
    text = _fix_leading_tense(text)
    for pattern, replacement in _INFORMAL:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"\.\s*\.", ".", text)
    text = re.sub(r"  +", " ", text)
    text = re.sub(r" @ ", " at ", text)
    if text and text[-1] not in ".!?":
        text += "."
    return text.strip()


def _is_garbage(text: str) -> bool:
    stripped = text.strip()
    if stripped in ("-", "--"):
        return False  # deliberate "nothing worth reporting" signal, not garbage
    low = text.lower()
    truncated = bool(re.search(r"\s[.!?]$", stripped))
    dangling_word = bool(re.search(r"\b(?:to|for|with|on|of|in|and|the|a|an)\.$", stripped, re.IGNORECASE))
    return truncated or dangling_word or len(text) < 10 or any(
        g in low for g in ("<", "your answer", "your response", "[insert", "n/a"))


_NUMERIC_TOKEN = re.compile(r"\b[A-Za-z]{0,4}\d[\w.-]*\b")


def _numeric_tokens(text: str) -> set[str]:
    return {t.lower() for t in _NUMERIC_TOKEN.findall(text)}


def _leaked_asset_id_digits(output: str, asset_id: str, original_body: str) -> bool:
    m = re.search(r"(\d{2,})$", asset_id or "")
    if not m:
        return False
    suffix = m.group(1)
    if re.search(rf"\b{re.escape(suffix)}\b", original_body):
        return False
    return bool(re.search(rf"\b{re.escape(suffix)}\b", output))


def _dropped_key_tokens(original_body: str, output: str, asset_id: str = "") -> set[str]:
    """Numeric/identifier tokens present in the input but missing from the
    output -- a reliable proxy for "the AI silently threw away a detail"."""
    dropped = _numeric_tokens(original_body) - _numeric_tokens(output)
    if asset_id:
        dropped.discard(asset_id.lower())
    return dropped


_PRESERVE_PART_NOUNS = {
    "fittings", "fitting", "brush", "brushes", "bearing", "bearings", "gasket", "gaskets",
    "valve", "valves", "coupler", "couplers", "coupling", "couplings", "hose", "hoses",
    "belt", "belts", "chain", "chains", "wheel", "wheels", "fuse", "fuses", "sensor",
    "sensors", "servo", "servos", "wire", "wires", "wiring",
}
_FITTING_VERB_FORM_RE = re.compile(r"\bfitting\s+(?:of|the)\b", re.IGNORECASE)


def _stem(word: str) -> str:
    return word[:-1] if word.endswith("s") and len(word) > 3 else word


def _dropped_part_nouns(original_body: str, output: str) -> set[str]:
    """Specific part nouns present in the input but silently swapped for a
    vaguer generic word in the output (e.g. "fittings" -> "components")."""
    orig_stems = {_stem(w) for w in re.findall(r"[a-zA-Z]+", original_body.lower())}
    output_for_check = _FITTING_VERB_FORM_RE.sub(" ", output.lower())
    out_stems = {_stem(w) for w in re.findall(r"[a-zA-Z]+", output_for_check)}
    preserve_stems = {_stem(w) for w in _PRESERVE_PART_NOUNS}
    return (orig_stems & preserve_stems) - out_stems


_NEW_OLD_PREP_RE = re.compile(r"\b(on|onto|from|to|using|with)\s+(?:the\s+)?(new|old)\b", re.IGNORECASE)


def _new_old_direction_reversed(original: str, output: str) -> bool:
    """"new X"/"old Y" swap-direction reversal guard -- catches the model
    flipping which part came from which piece of equipment."""
    def _prep_map(text: str) -> dict[str, set[str]]:
        m: dict[str, set[str]] = {}
        for prep, which in _NEW_OLD_PREP_RE.findall(text):
            m.setdefault(which.lower(), set()).add(prep.lower())
        return m

    def _role(preps: set[str]) -> str | None:
        has_from = "from" in preps
        has_dest = bool(preps - {"from"})
        if has_from and not has_dest:
            return "source"
        if has_dest and not has_from:
            return "dest"
        return None

    orig_map, out_map = _prep_map(original), _prep_map(output)
    if "new" not in orig_map or "old" not in orig_map:
        return False
    if "new" not in out_map or "old" not in out_map:
        return False
    orig_new, orig_old = _role(orig_map["new"]), _role(orig_map["old"])
    out_new, out_old = _role(out_map["new"]), _role(out_map["old"])
    if None in (orig_new, orig_old, out_new, out_old):
        return False
    return orig_new != out_new and orig_old != out_old


_INCOMPLETE_SIGNAL = re.compile(
    r"\b(?:unable to|could not|couldn't|was not able to|were not able to|"
    r"failed to|did not complete|didn't complete|not completed)\b",
    re.IGNORECASE,
)


def _claims_unwarranted_resolution(original: str, output: str) -> bool:
    """True if the technician flagged the work as incomplete/failed but the
    rewrite dropped that signal -- i.e. invented a resolution."""
    return bool(_INCOMPLETE_SIGNAL.search(original)) and not _INCOMPLETE_SIGNAL.search(output)


_WRONG_DOMAIN_WORDS = (
    "hyperlink", "webpage", "web page", "website", "web site", " url ",
    "browser", "web link", "internet link", "software update",
)


def _uses_wrong_domain_vocabulary(original: str, output: str) -> bool:
    orig_low, out_low = f" {original.lower()} ", f" {output.lower()} "
    return any(word in out_low and word not in orig_low for word in _WRONG_DOMAIN_WORDS)


_FLOWERY_PADDING_WORDS = (
    "hiccup", "accommodate", "endeavor", "endeavour", "utilize", "utilise",
    "leverage", "facilitate", "reconfigur", "stuck-up", "resultant", "myriad",
)


def _uses_flowery_padding_vocabulary(original: str, output: str) -> bool:
    orig_low, out_low = f" {original.lower()} ", f" {output.lower()} "
    return any(word in out_low and word not in orig_low for word in _FLOWERY_PADDING_WORDS)


# -- Ollama transport ----------------------------------------------------------

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(base_url=OLLAMA_BASE_URL, timeout=httpx.Timeout(_TIMEOUT, connect=5.0))
    return _client


def _detect_model() -> str | None:
    if OLLAMA_MODEL:
        return OLLAMA_MODEL
    try:
        resp = _get_client().get("/api/tags", timeout=5.0)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        return models[0]["name"] if models else None
    except Exception as exc:
        log.warning("[ai_remark_rewriter] Could not list Ollama models: %s", type(exc).__name__)
        return None


def check_ai_available() -> dict:
    """Readiness probe for the UI: can we actually offer AI rewrite right
    now? Never raises -- always returns a structured, explain-yourself
    result so a missing engine degrades to "ordinary spelling correction
    and manual editing continue", per the Phase 2 requirement."""
    if _MOCK_MODE:
        return {"available": True, "model": "mock", "reason": None}
    try:
        resp = _get_client().get("/api/tags", timeout=5.0)
        resp.raise_for_status()
    except Exception:
        return {"available": False, "model": None,
                "reason": "Local AI is unavailable. Confirm that Ollama is running "
                          f"at {OLLAMA_BASE_URL}."}
    model = _detect_model()
    if not model:
        return {"available": False, "model": None,
                "reason": "Ollama is running but no model is installed. "
                          "Run: ollama pull <model-name>."}
    return {"available": True, "model": model, "reason": None}


def _call_ollama(prompt: str, model: str) -> str | None:
    try:
        resp = _get_client().post(
            "/api/generate",
            json={
                "model": model,
                "system": _SYSTEM_PROMPT,
                "prompt": prompt,
                "stream": False,
                # Generous budget, not a tight one: ground-truth testing against
                # the actual installed model (gemma4:e2b) showed it reliably
                # consumes 300+ tokens before any visible text reaches the
                # `response` field at all (no fabricated content -- the field
                # is simply empty until the budget is large enough), the same
                # class of problem V3's ai_rewriter.py documented for Qwen3's
                # thinking mode. 700 was proven live to complete in ~15-20s
                # with room to spare; num_predict is a ceiling, not a target,
                # so a short real answer still returns quickly.
                "options": {"temperature": _TEMPERATURE, "num_predict": 700,
                            "stop": ["---", "\nOriginal:"]},
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return (resp.json().get("response") or "").strip() or None
    except httpx.TimeoutException:
        log.warning("[ai_remark_rewriter] Ollama call timed out after %.0fs", _TIMEOUT)
        return None
    except Exception as exc:
        log.warning("[ai_remark_rewriter] Ollama call failed: %s", type(exc).__name__)
        return None


def suggest_rewrite(remark: str, asset_id: str = "", wo_number: str = "") -> dict:
    """Suggest a professional rewrite of an already spelling-corrected
    remark. Never mutates anything and never auto-applies -- the result is
    only ever a suggestion for a human to review via the WO edit modal.

    Returns {"ok": True, "text": "..."} on a safe, guard-passing rewrite,
    or {"ok": False, "reason": "..."} when AI is unavailable, the call
    failed/timed out, or every safety guard rejected the output. ok=False
    is not an error to raise -- it is the expected, handled "no safe
    suggestion available" outcome; callers fall back to ordinary spelling
    correction and manual editing, exactly as if this feature did not
    exist for that one remark.
    """
    text = (remark or "").strip()
    if not text:
        return {"ok": False, "text": None, "reason": "No remark text to rewrite."}
    if len(text.split()) < _MIN_WORDS_FOR_AI:
        return {"ok": False, "text": None,
                "reason": "Remark is too short for an AI rewrite to add value safely."}

    if _MOCK_MODE:
        raw = _MOCK_RESPONSE or f"{text} (mock AI rewrite)"
    else:
        availability = check_ai_available()
        if not availability["available"]:
            return {"ok": False, "text": None, "reason": availability["reason"]}
        raw = _call_ollama(_SINGLE_TEMPLATE.format(text=text), availability["model"])

    if not raw or _is_garbage(raw):
        return {"ok": False, "text": None,
                "reason": "The local AI could not produce a usable rewrite for this remark."}

    cleaned = _clean_output(_strip_leading_asset_id(raw, asset_id))
    cleaned = _strip_leaked_context_line(cleaned)
    cleaned = _strip_invented_component_labels(cleaned, text)
    cleaned = _strip_vague_equipment_phrase(cleaned)
    cleaned = _strip_technician_subject(cleaned)
    cleaned = _strip_first_person(cleaned)

    if cleaned.strip().rstrip(".!?") in ("-", "--", ""):
        return {"ok": False, "text": None,
                "reason": "AI found no additional technical content worth rewriting."}

    for guard, label in (
        (_leaked_asset_id_digits(cleaned, asset_id, text), "leaked the asset ID into the text"),
        (_dropped_key_tokens(text, cleaned, asset_id), "dropped an identifier or measurement"),
        (_dropped_part_nouns(text, cleaned), "replaced a specific part name with a vaguer word"),
        (_new_old_direction_reversed(text, cleaned), "reversed which part came from which equipment"),
        (_claims_unwarranted_resolution(text, cleaned), "invented a resolution that was not stated"),
        (_uses_wrong_domain_vocabulary(text, cleaned), "used non-maintenance vocabulary"),
        (_uses_flowery_padding_vocabulary(text, cleaned), "added vague corporate filler"),
    ):
        if guard:
            log.info("[ai_remark_rewriter] Rewrite rejected by safety guard: %s", label)
            return {"ok": False, "text": None,
                    "reason": "AI suggestion was rejected by a safety check "
                              f"({label}). Original remark is unchanged."}

    return {"ok": True, "text": cleaned, "reason": None}
