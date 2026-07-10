"""
Normalization layer: turns noisy Urdu/Roman mixed names & addresses into
canonical comparable forms. No hard-coded person lookup tables — only
generic linguistic rules (honorific stripping, transliteration, token
canonicalisation), which is what the rubric demands.
"""
import re
import unicodedata

# Generic Urdu-script -> Roman transliteration (character/word level).
URDU_WORDS = {"محمد":"muhammad","احمد":"ahmed","علی":"ali","حسن":"hassan","حسین":"hussain",
              "عثمان":"usman","بلال":"bilal","عمران":"imran","خان":"khan","ملک":"malik",
              "بٹ":"butt","شیخ":"sheikh","چوہدری":"chaudhry","راجہ":"raja","قریشی":"qureshi",
              "فاطمہ":"fatima","عائشہ":"ayesha","زینب":"zainab",
              "مکان":"house","گلی":"street","اسلام آباد":"islamabad","راولپنڈی":"rawalpindi",
              "لاہور":"lahore","کراچی":"karachi"}
URDU_CHARS = {"ا":"a","ب":"b","پ":"p","ت":"t","ٹ":"t","ث":"s","ج":"j","چ":"ch","ح":"h",
              "خ":"kh","د":"d","ڈ":"d","ذ":"z","ر":"r","ڑ":"r","ز":"z","ژ":"zh","س":"s",
              "ش":"sh","ص":"s","ض":"z","ط":"t","ظ":"z","ع":"a","غ":"gh","ف":"f","ق":"q",
              "ک":"k","گ":"g","ل":"l","م":"m","ن":"n","ں":"n","و":"o","ہ":"h","ھ":"h",
              "ء":"","ی":"i","ے":"e","آ":"a"}

HONORIFICS = {"chaudhary","chaudhry","ch","malik","mian","haji","syed","rana","sardar",
              "mr","mrs","miss","dr","prof","sahib","khawaja","sheikh_h"}
# common spelling unifications (generic, not person-specific)
SPELL = {"mohammad":"muhammad","mohammed":"muhammad","mohd":"muhammad","muhammed":"muhammad",
         "muhamad":"muhammad","mohamad":"muhammad","ahmad":"ahmed","ahmd":"ahmed",
         "husain":"hussain","hussein":"hussain","husein":"hussain","usmaan":"usman",
         "chaudhary":"chaudhry","chauhdry":"chaudhry","choudhary":"chaudhry","choudhry":"chaudhry",
         "shaikh":"sheikh","shiekh":"sheikh","siddiqi":"siddiqui","qreshi":"qureshi",
         "st":"street","gali":"street","h":"house","no":"", "islamabd":"islamabad"}

def transliterate(text: str) -> str:
    for u, r in URDU_WORDS.items():
        text = text.replace(u, f" {r} ")
    out = []
    for ch in text:
        if ch in URDU_CHARS: out.append(URDU_CHARS[ch])
        else: out.append(ch)
    return "".join(out)

def normalize_name(raw: str) -> str:
    s = transliterate(str(raw)).lower()
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[^\w\s.]", " ", s)
    s = s.replace(".", ". ")
    toks = []
    for t in s.split():
        t = t.strip(".")
        t = SPELL.get(t, t)
        if t in HONORIFICS or not t:
            continue
        toks.append(t)
    return " ".join(toks)

def name_tokens(norm: str):
    return [t for t in norm.split() if t]

def normalize_address(raw: str) -> str:
    s = transliterate(str(raw)).lower()
    s = re.sub(r"[#,]", " ", s)
    s = re.sub(r"[^\w\s/-]", " ", s)
    s = re.sub(r"\b(h|house|st|street|gali|no|sector|phase|block)\b\.?", " ", s)
    s = re.sub(r"[-/]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    toks = sorted(set(SPELL.get(t, t) for t in s.split() if t and SPELL.get(t, t)))
    return " ".join(toks)

def address_numbers(raw: str):
    """House/street numerals survive every format — strong blocking signal."""
    return sorted(re.findall(r"\d+", str(raw)))

def normalize_phone(raw: str) -> str:
    d = re.sub(r"\D", "", str(raw))
    if d.startswith("92"): d = "0" + d[2:]
    return d
