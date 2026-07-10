"""
LLM edge services (C14 narrator, C15 address parser, C16 match adjudicator).

Principle: LLM at the edges, deterministic core. Every function returns None
(or a safe fallback verdict) when no API key is configured, the network is
down, or the response fails validation — the pipeline then uses its
deterministic path. All results are cached on disk so repeated runs make zero
duplicate calls.

Providers: set GROQ_API_KEY (uses llama-3.3-70b-versatile) or GEMINI_API_KEY
(uses gemini-2.0-flash). Groq's free tier is fast enough for live demos.
"""
import hashlib
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "..", "data", ".llm_cache.json")

def _load_cache():
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

_CACHE = _load_cache()

def _save_cache():
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_CACHE, f, ensure_ascii=False)
    except Exception:
        pass

def available() -> bool:
    return bool(os.environ.get("GROQ_API_KEY") or os.environ.get("GEMINI_API_KEY"))

def _call(system: str, user: str, max_tokens: int = 600) -> str | None:
    """Single completion against whichever provider is configured."""
    key = hashlib.sha256((system + "\x00" + user).encode()).hexdigest()
    if key in _CACHE:
        return _CACHE[key]
    out = None
    try:
        if os.environ.get("GROQ_API_KEY"):
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=json.dumps(dict(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    temperature=0, max_tokens=max_tokens)).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                out = json.load(r)["choices"][0]["message"]["content"]
        elif os.environ.get("GEMINI_API_KEY"):
            url = ("https://generativelanguage.googleapis.com/v1beta/models/"
                   f"gemini-2.0-flash:generateContent?key={os.environ['GEMINI_API_KEY']}")
            req = urllib.request.Request(url, data=json.dumps(dict(
                systemInstruction=dict(parts=[dict(text=system)]),
                contents=[dict(parts=[dict(text=user)])],
                generationConfig=dict(temperature=0, maxOutputTokens=max_tokens))).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                out = json.load(r)["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return None
    if out is not None:
        _CACHE[key] = out
        _save_cache()
    return out

def _strict_json(text: str):
    try:
        t = text.strip()
        if t.startswith("```"):
            t = t.split("```")[1].lstrip("json").strip()
        return json.loads(t)
    except Exception:
        return None

# ---------------------------------------------------------------- C15 parser
PARSE_SYS = ("You extract structured Pakistani address fields. Respond with ONLY a "
             "JSON object {\"house_no\": str|null, \"street\": str|null, "
             "\"sector_or_area\": str|null, \"city\": str|null}. The address may mix "
             "Urdu and English script. Use null for absent fields. No other text.")

def parse_address(raw: str):
    """Returns dict or None. Caller keeps rule-based result on None."""
    if not available():
        return None
    out = _call(PARSE_SYS, raw, max_tokens=120)
    if out is None:
        return None
    j = _strict_json(out)
    if isinstance(j, dict) and set(j) >= {"house_no", "street", "sector_or_area", "city"}:
        return j
    return None

# ----------------------------------------------------------- C16 adjudicator
ADJ_SYS = ("You adjudicate whether two Pakistani government records refer to the SAME "
           "person. Names/addresses mix Urdu and English script, honorifics, and "
           "initials. Respond with ONLY JSON {\"verdict\": \"match\"|\"no_match\"|"
           "\"uncertain\", \"reason\": \"<one short sentence>\"}. Be conservative: "
           "common names (e.g. Muhammad Khan) with weak corroboration are 'uncertain'.")

def adjudicate_pair(a: dict, b: dict):
    """Returns ('match'|'no_match'|'uncertain', reason). Without a key configured,
    returns ('uncertain', ...) so the pair lands in the human-review queue."""
    if not available():
        return "uncertain", "no adjudicator configured; routed to human review"
    user = json.dumps(dict(
        record_1=dict(source=a["source"], name=a["raw_name"], address=a["raw_addr"]),
        record_2=dict(source=b["source"], name=b["raw_name"], address=b["raw_addr"])),
        ensure_ascii=False)
    out = _call(ADJ_SYS, user, max_tokens=120)
    j = _strict_json(out) if out else None
    if isinstance(j, dict) and j.get("verdict") in ("match", "no_match", "uncertain"):
        return j["verdict"], str(j.get("reason", ""))[:200]
    return "uncertain", "adjudicator response invalid; routed to human review"

# -------------------------------------------------------------- C14 narrator
NARR_SYS = ("You write formal tax-audit narratives for FBR auditors. You receive "
            "structured evidence JSON. STRICT RULE: reference ONLY facts present in "
            "the input — never invent numbers, names, assets, or claims. Respond "
            "with ONLY JSON {\"english\": \"<narrative>\", \"urdu\": \"<formal Urdu "
            "narrative, RTL script>\"}. 4-7 sentences each, professional tone.")

def narrate(payload: dict):
    """payload: {name, tier, final, components, weights, evidence[], facts{}}.
    Returns {'english':..,'urdu':..} or None (caller uses template)."""
    if not available():
        return None
    out = _call(NARR_SYS, json.dumps(payload, ensure_ascii=False), max_tokens=900)
    j = _strict_json(out) if out else None
    if isinstance(j, dict) and j.get("english") and j.get("urdu"):
        return dict(english=str(j["english"]), urdu=str(j["urdu"]))
    return None
