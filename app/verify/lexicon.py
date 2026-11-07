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
]
# a test mention followed by one of these is being defined ("ALT is an enzyme", "the HbA1c measures ...")
DEFINITION_AFTER_TEST = r"^\s*(?:\([^)]*\)\s*)?(?:is\s+(?:a|an)\s|are\s+(?:a|an)\s|measures?\b)"
# EXEMPT: deferring to a doctor
DEFERRALS = [
    r"\b(?:a|your|the)\s+(?:doctor|physician|clinician|healthcare\s+provider)\s+(?:would|will|can|may|might|should|could)\b",
    r"\bask\s+(?:your|a)\s+(?:doctor|physician|clinician)\b",
    r"\b(?:discuss|talk)\s+(?:\w+\s+){0,3}with\s+(?:your|a)\s+(?:doctor|physician|clinician)\b",
]
