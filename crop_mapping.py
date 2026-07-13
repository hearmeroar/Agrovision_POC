"""
Cross-language canonical crop mapping between the Slovenian (EuroCrops SI)
and Slovak (EuroCrops SK) crop-declaration names bundled in this project.

Why this exists: EuroCrops preserves each country's *original* crop name, so
the same real-world crop is two different text strings across the two
samples — "pšenica (ozimna)" (SI) and "Pšenica letná ozimná" (SK) are both
winter wheat. Without this mapping:
  - a weak benchmark (few fields of that crop in one country) can't borrow
    strength from the other country's fields of the same crop.
  - the "best alternative crop" suggestion could flag a field as a likely
    misdeclaration just because its own-language crop name doesn't string-
    match a benchmark that is, in reality, the exact same crop.

Only entries observed in the two bundled samples are covered — this is not
a general EuroCrops/HCAT crop ontology.
"""

CANONICAL_CROP = {
    # --- Slovenia (SI) ---
    "koruza za zrnje": "corn_grain",
    "pšenica (ozimna)": "wheat_winter",
    "ječmen (ozimni)": "barley_winter",
    "koruza za silažo": "corn_silage",
    "trajno travinje": "grassland_permanent",
    "soja": "soybean",
    "oljna buča": "pumpkin_oil",
    "tritikala (ozimna)": "triticale_winter",
    "oljna ogrščica (ozimna)": "rapeseed_winter",
    "rž (ozimna)": "rye_winter",
    "zelenjadnice": "vegetables",
    "mešane sadne vrste": "fruit_mixed",
    "inkarnatka": "clover_crimson",
    "krompir - pozni": "potato_late",
    "mešana raba (zelenjadnice, poljščine, dišavnice in zdravilna zelišča)": "mixed_use",
    "vrtni mak (ozimni)": "poppy_winter",
    "sladkorna pesa": "sugarbeet",
    "oves (ozimni)": "oats_winter",
    "miskant": "miscanthus",
    "konoplja": "hemp",
    "praha s posevkom brez kmetijske proizvodnje": "fallow_with_cover",
    "sončnice": "sunflower",
    "detelja": "clover",

    # --- Slovakia (SK) ---
    "Pšenica letná ozimná": "wheat_winter",
    "Kukurica": "corn_grain",
    "Pôda ležiaca úhorom s porastom": "fallow_with_cover",
    "Jačmeň jarný": "barley_spring",
    "Jačmeň ozimný": "barley_winter",
    "Slnečnica ročná": "sunflower",
    "Kapusta repková pravá - ozimná": "rapeseed_winter",
    "Lucerna siata": "alfalfa",
    "Tritikale": "triticale_winter",
    "Trvalý trávny porast": "grassland_permanent",
    "Biopás pre medonosné plodiny": "bee_forage_strip",
    "Biopás": "bee_forage_strip",
    "Cirok": "sorghum",
    "Pôda ležiaca úhorom pre medonosné plodiny": "fallow_bee_forage",
    "Kukurica na siláž": "corn_silage",
    "Pšenica špaldová": "wheat_spelt",
    "Hrach siaty": "peas",
    "Zelenina a iné záhradné plodiny voľne pestované": "vegetables",
    "Zmiešaná zelenina": "vegetables",
    "Rajčiak jedlý": "tomato",
    "Pšenica tvrdá ozimná": "wheat_durum_winter",
    "Trávy alebo iné bylinné krmoviny": "grass_forage",
    "Reďkev siata olejná (iná ako na priamy konzum)": "radish_oil",
    "Ovos siaty": "oats",
    "Ďatelina lúčna": "clover",
    "Jahody": "strawberry",
    "Horčica biela": "mustard_white",
    "Šalvia lekárska": "sage",
    "Zemiaky konzumné (neskoré)": "potato_late",
    "Mrkva obyčajná": "carrot",
    "Pôda ležiaca úhorom": "fallow_bare",
    "Pšenica tvrdá jarná": "wheat_durum_spring",
    "Cesnak (zimný)": "garlic",
    "Sója fazuľová": "soybean",
    "Pšenica letná jarná": "wheat_spring",
    "Strukovinovo-obilná miešanka": "legume_cereal_mix",
    "Slivka domáca": "plum",
    "Kukurica cukrová": "corn_sweet",
    "Dyňa červená": "watermelon",
    "Cibuľa (jarná)": "onion",
    "Petržlen záhradný": "parsley",
}


def canonical_crop(raw_name: str) -> str:
    """
    Canonical crop key for a raw declared crop name, or the raw name itself
    if it isn't in the mapping — unmapped crops still work standalone, just
    without cross-country pooling/dedup.
    """
    return CANONICAL_CROP.get(raw_name, raw_name)


# English display names, keyed by the canonical crop key above (not the raw
# SI/SK string) so both languages' spelling of the same crop share one label.
CROP_DISPLAY_NAME = {
    "corn_grain": "Corn (grain)",
    "wheat_winter": "Winter wheat",
    "barley_winter": "Winter barley",
    "corn_silage": "Corn (silage)",
    "grassland_permanent": "Permanent grassland",
    "soybean": "Soybean",
    "pumpkin_oil": "Oil pumpkin",
    "triticale_winter": "Winter triticale",
    "rapeseed_winter": "Winter rapeseed",
    "rye_winter": "Winter rye",
    "vegetables": "Vegetables",
    "fruit_mixed": "Mixed fruit",
    "clover_crimson": "Crimson clover",
    "potato_late": "Late potato",
    "mixed_use": "Mixed use (vegetables/field crops/herbs)",
    "poppy_winter": "Winter poppy",
    "sugarbeet": "Sugar beet",
    "oats_winter": "Winter oats",
    "miscanthus": "Miscanthus",
    "hemp": "Hemp",
    "fallow_with_cover": "Fallow with cover crop",
    "sunflower": "Sunflower",
    "clover": "Clover",
    "barley_spring": "Spring barley",
    "alfalfa": "Alfalfa",
    "wheat_spelt": "Spelt wheat",
    "bee_forage_strip": "Bee forage strip",
    "sorghum": "Sorghum",
    "fallow_bee_forage": "Fallow (bee forage)",
    "wheat_durum_winter": "Winter durum wheat",
    "grass_forage": "Grass / forage herbs",
    "radish_oil": "Oil radish",
    "oats": "Oats",
    "peas": "Peas",
    "tomato": "Tomato",
    "strawberry": "Strawberry",
    "mustard_white": "White mustard",
    "sage": "Sage",
    "carrot": "Carrot",
    "fallow_bare": "Bare fallow",
    "wheat_durum_spring": "Spring durum wheat",
    "garlic": "Garlic (winter)",
    "wheat_spring": "Spring wheat",
    "legume_cereal_mix": "Legume-cereal mix",
    "plum": "Plum",
    "corn_sweet": "Sweet corn",
    "watermelon": "Watermelon",
    "onion": "Onion (spring)",
    "parsley": "Parsley",
}


def display_crop_name(raw_name: str) -> str:
    """
    English label for a raw declared crop name, for anything shown to the
    user (field picker, chart titles, match-score messages). Falls back to
    the raw name itself for crops outside the two bundled samples (e.g.
    "Unknown") rather than hiding that a crop is untranslated.
    """
    return CROP_DISPLAY_NAME.get(canonical_crop(raw_name), raw_name)
