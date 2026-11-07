"""Interpretation lexicon for MedLens Verify (data, not code).

A sentence of an AI explanation is OVERSTEPS when it contains a CONDITION/STATE term below and is not EXEMPT.
Every entry is a regular-expression fragment (case-insensitive). Extend the lists; the rule in claims.py stays
the same. Glossary entries of kind "finding" are added automatically.
"""

# words ending like a condition name; NOT_CONDITION_WORDS are ordinary words with the same ending
CONDITION_SUFFIXES = ["aemia", "emia", "itis", "osis", "cytosis", "penia", "pathy", "megaly", "lipidemia", "lipidaemia"]
NOT_CONDITION_WORDS = ["diagnosis", "prognosis", "sympathy", "empathy", "academia", "telepathy"]

# diseases and disease categories
CONDITIONS = [
    r"an(?:a)?emi(?:a|c)", r"pre-?diabet(?:es|ic)", r"diabet(?:es|ic)", r"impaired\s+fasting\s+glucose",
    r"insulin\s+resistance", r"metabolic\s+syndrome", r"[\w']+\s+syndrome", r"dyslipid(?:a)?emia", r"fatty\s+liver",
    r"gout", r"kidney\s+stones?", r"gall\s*stones?", r"hepatitis(?:\s+[a-e])?", r"infections?", r"deficienc(?:y|ies)",
    r"illness(?:es)?", r"diseases?", r"disorders?", r"hypothyroidism", r"hyperthyroidism", r"cancer", r"tumou?rs?",
    # inflammation as a state of the body (not "an inflammation marker")
    r"uti", r"urinary\s+tract\s+infections?", r"hypothyroid", r"hyperthyroid", r"thyrotoxicosis",
    r"(?:systemic|chronic|mild|low-grade|joint|underlying)\s+inflammation", r"inflammation\s+in\s+the\s+body",
    r"level\s+of\s+(?:systemic\s+)?inflammation", r"inflamed",
]
# a condition word right before one of these names a panel or a test, not a condition ("diabetes panel")
NOT_CONDITION_AFTER = r"\s+(?:panel|profile|tests?|screen(?:ing)?|markers?|clinic|educator)\b"
# a condition word right after one of these is what something protects against ("fight infection")
NOT_CONDITION_BEFORE = r"\b(?:fights?|fighting|against|prevents?|preventing|protects?|protecting)\s+(?:\w+\s+)?$"

ORGANS = r"(?:liver|kidneys?|renal|thyroid|heart|cardiac|hepatic|pancreatic|pancreas|lungs?|bone|bones|metabolic)"
# state of an organ or the body
ORGAN_STATES = [
    rf"(?:decreased|impaired|reduced|normal|healthy|good|poor|declining|weak|weakened)\s+(?:\w+\s+)?{ORGANS}\s+"
    r"(?:function(?:ing)?|filtration|health)",
    r"functions?\s+of\s+(?:these|the|your)\s+organs?\s+(?:are|is)\s+intact",
    r"organs?\s+(?:are|is)\s+(?:intact|working\s+(?:well|normally))",
    rf"stress\s+on\s+(?:the|your)\s+{ORGANS}",
    rf"{ORGANS}\s+(?:is|are)\s+(?:\w+\s+){{0,5}}?(?:sluggish(?:ly)?|struggling|overworked|stressed|under\s+strain)",
    r"processing\s+(?:\w+\s+){0,4}?sluggishly",
]
# health judgements about the person, the markers or the organs ("perfectly healthy", "in good health")
HEALTH_JUDGEMENTS = [
    r"perfectly\s+healthy",
    r"(?:is|are|look|looks|seem|seems)\s+(?:all\s+|perfectly\s+|completely\s+|very\s+)*healthy(?!\s+(?:range|level|limit))",
    r"in\s+(?:good|excellent|perfect)\s+health",
    r"cardiovascular\s+health",
    r"health\s+trends?",
    r"(?:you|your\s+body)\s+(?:is|are|look|seem)s?\s+(?:\w+\s+)?well-balanced",
    r"no\s+active\s+[\w\s]{0,30}?(?:infection|disease|hepatitis)",
]
# causes and prognosis (not when the cause is about the data: a typo, units, rounding)
CAUSES = [
    r"caused\s+by(?!\s+(?:a\s+)?(?:typo|rounding|units?|formatting))",
    r"due\s+to(?!\s+(?:a\s+)?(?:typo|rounding|units?|formatting|the\s+lab))",
    r"commonly\s+from", r"causing", r"can\s+lead\s+to", r"lead\s+to", r"progress(?:es|ing)?\b",
    r"reverse\s+the\s+trend", r"recovery\s+from", r"(?:a|the)\s+result\s+of",
]
# lifestyle and treatment advice (not when deferred to a doctor)
ADVICE = [
    r"diet(?:ary)?\s+changes?", r"exercise", r"weight\s+(?:management|loss)", r"lifestyle\s+changes?",
    r"supplements?", r"cut\s+(?:down\s+)?on\s+\w+",
]

# EXEMPT: definitions of what a test is or measures
DEFINITIONS = [
    r"\b(?:is|are)\s+(?:a|an)\s+(?:\w+\s+){0,3}?(?:marker|measure|indicator)\s+(?:for|of)\b",
    r"\b(?:is|are)\s+(?:produced|made|found|formed|released|stored)\b",
    r"\b(?:this|it|which)\s+measures?\b",
    r"\bis\s+another\s+(?:word|name|term)\s+for\b",
    # the purpose of a test: "used to screen for diabetes", "ordered this test to check for anemia"
    r"\b(?:used|ordered|done|performed|requested)\s+(?:\w+\s+){0,3}?to\s+(?:screen|check|test|look|detect|monitor|rule\s+out)"
    r"(?:\s+for)?\b",
    r"\bto\s+(?:screen|check|test)\s+for\b",
    r"\b(?:looks?|checks?|screens?|tests?)\s+for\b", r"\bhelps?\s+(?:to\s+)?(?:assess|detect|check|measure|find|investigate)\b",
    r"\bto\s+(?:investigate|assess|evaluate|look\s+for|detect)\b",
    # defining a condition word itself: "Anemia means ...", "Gout is a ...", "Fatty liver refers to ..."
    r"^\W*(?:[A-Za-z'-]+\s+){0,2}?(?:means|refers\s+to|describes|is\s+(?:a|an|the)\s+(?:term|word|name|condition))\b",
]
# a test mention followed by one of these is being defined ("ALT is an enzyme", "the HbA1c measures ...")
DEFINITION_AFTER_TEST = r"^\s*(?:\([^)]*\)\s*)?(?:is\s+(?:a|an)\s|are\s+(?:a|an)\s|measures?\b)"
# EXEMPT: deferring to a doctor
DEFERRALS = [
    r"\b(?:a|your|the)\s+(?:doctor|physician|clinician|healthcare\s+provider)\s+(?:would|will|can|may|might|should|could)\b",
    r"\bask\s+(?:your|a)\s+(?:doctor|physician|clinician)\b",
    r"\b(?:discuss|talk)\s+(?:\w+\s+){0,3}with\s+(?:your|a)\s+(?:doctor|physician|clinician)\b",
]

# ---------------------------------------------------------------- quotes and certainty
# glossary findings that name the same thing (a report's "calculus" is the AI's "stone"/"gallstone")
FINDING_SYNONYMS = [
    ["calculus", "cholelithiasis"],
    ["fatty_liver", "hepatomegaly"],
]
# the AI keeps the uncertainty when one of these is present
AI_HEDGES = [
    r"likely", r"possibl[ey]", r"probabl[ey]", r"may", r"might", r"could", r"suggest(?:s|ive|ing)?", r"\?",
    r"cannot\s+be\s+ruled\s+out", r"consistent\s+with", r"appears?", r"seems?", r"suspected", r"suspicion",
    r"if", r"whether", r"not\s+confirmed", r"concern\s+for", r"query",
]
# the AI is talking about this patient (not explaining a word in general)
PATIENT_REFERENCE = [
    r"there\s+(?:is|are)", r"you\s+have", r"your", r"this\s+(?:condition|finding|stone|is)", r"is\s+called",
    r"the\s+patient", r"(?:report|scan|ultrasound)\s+shows", r"measuring", r"inside\s+the",
]
# the AI attributes a hedged finding to the report ("a mention of possible ...")
ATTRIBUTION = [r"mention(?:s|ed)?\s+of", r"the\s+report\s+(?:mentions|notes|says)", r"radiologist\s+(?:mentions|notes|says)"]

# ---------------------------------------------------------------- sentence types that are never claims
QUESTION_START = (r"^\W*(?:\d+[.)]\s*)?(?:[\w' /-]{1,30}:\s*)?(?:have|do|does|is|are|did|can|could|has|was|were|will|would|"
                  r"should)\s+(?:you|your|the|there|it|this|that|a|an|any|anyone)\b")
SAFETY_ADVICE = [
    r"\b(?:don't|do\s+not|never)\s+(?:start|stop|change|take|skip|begin|increase|decrease|double)\b[^.!?]*\bwithout\b",
    r"\bseek\s+(?:\w+\s+){0,3}?(?:care|attention|help|assessment|evaluation|advice|treatment)\b",
    r"\bavoid\s+(?:taking\s+)?(?:high|large|extra|mega)\s+doses?\b",
    r"\b(?:call|go\s+to)\s+(?:an?\s+|the\s+)?(?:emergency|ambulance|er|a&e|hospital)\b",
]
# "X means ...", "X is another word for ...", "X is a/an ...": defining a word (no "your"/"you")
DEFINITION_SENTENCE = (r"^(?!\W*(?:there|it|this|that|these|those|which|here)\b)"
                       r"\W*(?:an?\s+|the\s+)?[\w'() -]{1,40}?\s+(?:means|refers\s+to|is\s+another\s+(?:word|name|term)\s+for|"
                       r"is\s+(?:a|an)\s)")
PATIENT_RESULT = r"\b(?:you|your|yours|this\s+(?:result|value|level|pattern)|these\s+(?:results|values|levels))\b"

# ---------------------------------------------------------------- recall by class (held-out round 1)
# REASSURANCE: telling the patient how serious (or not) things are, e.g. "Good news: this is easily manageable."
REASSURANCE = [
    r"reassuring", r"encouraging", r"don'?t\s+(?:worry|panic)", r"do\s+not\s+(?:worry|panic)", r"no\s+need\s+to\s+(?:worry|panic)",
    r"nothing\s+(?:here\s+)?(?:\w+\s+){0,3}?(?:serious|dangerous|alarming|worrying)", r"nothing\s+to\s+worry\s+about",
    r"(?:generally\s+|usually\s+|very\s+|easily\s+|highly\s+)?treatable", r"(?:generally\s+|usually\s+|easily\s+)?manageable",
    r"good\s+news",
]
# LIFESTYLE IMPERATIVES: an imperative verb + a lifestyle object, e.g. "Cut down on salty snacks."
LIFESTYLE_VERBS = ["reduce", "avoid", "limit", "eat", "drink", "exercise", "maintain", "lose", "cut", "increase", "stop",
                   "quit", "follow", "include", "choose", "walk", "add", "swap", "try"]
LIFESTYLE_OBJECTS = [r"diet", r"sugar", r"sugary", r"sweets?", r"alcohol", r"fried", r"processed", r"foods?", r"weight",
                     r"activity", r"exercise", r"fibre", r"fiber", r"water", r"salt", r"salty", r"smoking", r"sleep", r"meals?",
                     r"minutes", r"fats?", r"vegetables", r"fruits?", r"caffeine", r"snacks?", r"drinks?", r"oily"]
# ORGAN FUNCTION: what an organ is doing, e.g. "Your liver may be producing less albumin than usual."
ORGAN_FUNCTION = [
    rf"(?:over|under)active\s+{ORGANS}", rf"{ORGANS}\s+(?:over|under)activity",
    rf"{ORGANS}(?:\s+gland)?\s+(?:may\s+be|might\s+be|could\s+be|is|seems)\s+(?:\w+\s+){{0,2}}?(?:over|under)active",
    rf"{ORGANS}(?:\s+gland)?\s+(?:may\s+be\s+|might\s+be\s+|is\s+)?(?:producing|making|releasing)\s+(?:more|less|too\s+much|"
    r"too\s+little|excess|extra)",
    r"(?:producing|making)\s+more\s+[\w\s]{0,30}?than\s+(?:your|the)\s+body\s+needs",
    r"function(?:ing)?\s+less\s+(?:efficiently|well)",
]
# OUTSIDE CUT-OFF LISTS: bullets of "<number or range>: <category>", e.g. "- Under 12: Deficient"
CUTOFF_CATEGORIES = (r"very\s+low|very\s+high|low|normal|adequate|insufficient|insufficiency|deficient|deficiency|desirable|"
                     r"borderline(?:\s+high|\s+low)?|high|optimal|sufficient|toxic|elevated|ideal")
