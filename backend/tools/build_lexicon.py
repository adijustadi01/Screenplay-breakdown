"""Build the built-in English dictionary used by the offline reader.

Reads the raw Princeton WordNet 3.0 files and writes app/lexicon/words.tsv.gz: one line per
English word form (plurals and verb endings included, so the app needs no grammar code at run time):

    form <TAB> code <TAB> tag <TAB> lemma <TAB> pos

code   what the noun mainly means (its most common meaning)
         P person ("CONSTABLE", "GOONS")      p can be a person, usually is not ("STAR")
         G group of people ("CROWD")          A animal ("DOG")       O anything else ("MIRROR")
         empty when the form is not a noun
tag    what a production department would call it, from the dictionary's "is a kind of" tree:
         veh:car  veh:truck ...   animal:dog ...   cos:everyday  cos:accessory ...   inst
         prop:weapon  prop:food_drink  prop:bag  prop:tool ...   body   (empty = none)
lemma  the singular, when the form is a plural ("knives" -> "knife")
pos    which parts of speech the form can be: n noun, v verb (base), z verb with -s ("rings"),
       w other verb forms ("ringing", "rang"), a adjective, r adverb

Run once (the output is committed; the app never downloads anything):

    python tools/build_lexicon.py /path/to/wordnet          # the folder holding index.noun

WordNet: https://wordnet.princeton.edu  (licence copied to app/lexicon/WORDNET_LICENSE.txt)
"""
from __future__ import annotations

import gzip
import shutil
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "app" / "lexicon"
PERSON, GROUP, ANIMAL, BODY, FOOD, ARTIFACT = 18, 14, 5, 8, 13, 6
# Group senses that mean people ("crowd", "mob") rather than collections of things ("bunch").
PEOPLE_GROUP_HINTS = ("people", "persons", "person", "men", "soldiers", "workers", "members", "individuals",
                      "followers", "players", "crowd")

# Words whose dictionary meaning is right but which a breakdown should ignore: parts of things,
# vague words, set structure, and words whose everyday script meaning differs ("cricket", "tank").
STOP = set("""
pocket sleeve collar cuff bath bathtub flat well deck bunk stoop cast mold mould accelerator axle handlebar
lever mast trigger shaft rein crupper china pottery charcoal accessory array laundry washing weeds spectator
habit lid wrap wrapper junk heap tank carrier craft rocket fighter pirate chopper bark cricket charger animal
creature beast mammal vertebrate pet female male adult young offspring giant mutant survivor human stray mount
prey predator host parasite worker soldier queen drone monarch bacteria virus germ microorganism protozoa
algae larva game stud tick mite swallow fawn thing object piece part item stuff set kit gear rig device
instrument implement tool equipment apparatus machine machinery contraption gadget vehicle conveyance weapon
clothing clothes garment outfit attire costume dress food meal drink beverage container receptacle utensil
document publication vessel transport round rounds shot bit means line mark sign point top
bottom side end front film still negative goal mound buck weight tile
sequence setup iron cooler special western mixture concentrate ingredient produce provisions diet feed fiber
filling morsel mouthful slice wedge snap pop kiss jumble mess bite course dish helping serving treat board
stand base block bar frame post rail rack plug prop cross plant corner center outlet joint coil hoop knob
key
""".split())

# Common script words the dictionary files under an abstract meaning first, or gets wrong for a
# breakdown, and Indian words it does not know. These always win.
EXTRA = {
    # documents and papers (the dictionary treats them as "communication", not objects)
    **dict.fromkeys("""letter letters newspaper newspapers magazine magazines file files document documents paper
        papers certificate certificates passport passports ticket tickets map maps diary diaries notebook notebooks
        ledger ledgers envelope envelopes postcard postcards poster posters pamphlet pamphlets cheque cheques
        receipt receipts licence license invitation invitations telegram telegrams book books bible register
        registers chart charts form forms report reports note notes leaflet leaflets banner banners placard
        placards signboard signboards menu menus album albums calendar calendars textbook textbooks script scripts
        contract contracts affidavit will warrant summons fir chit chits slip slips card cards id""".split(),
                    "prop:document"),
    **dict.fromkeys("photograph photographs photo photos picture pictures portrait portraits painting paintings "
                    "sketch sketches drawing drawings snapshot snapshots".split(), "prop:photograph"),
    **dict.fromkeys("phone phones mobile mobiles cellphone cellphones telephone telephones smartphone smartphones "
                    "landline handset handsets".split(), "prop:phone"),
    **dict.fromkeys("""television televisions tv tvs radio radios transistor camera cameras laptop laptops computer
        computers tablet microphone microphones mike mic megaphone megaphones loudspeaker loudspeakers speaker
        speakers headphones earphones walkie-talkie recorder stopwatch calculator projector remote
        scoreboard""".split(), "prop:electronics"),
    **dict.fromkeys("money cash coin coins rupee rupees banknote banknotes currency wallet wallets purse purses "
                    "wad".split(), "prop:money"),
    **dict.fromkeys("key keys keychain".split(), "prop:key"),
    **dict.fromkeys("""knife knives lathi lathis baton batons sword swords dagger daggers axe axes machete sickle
        sickles trident spear spears bow arrow arrows gun guns pistol pistols revolver revolvers rifle rifles
        grenade grenades bomb bombs bullet bullets cartridge cartridges whip whips club clubs cleaver blade blades
        katta tamancha""".split(), "prop:weapon"),
    **dict.fromkeys("""chai tea coffee milk lassi juice roti rotis chapati chapatis paratha parathas dal sabzi
        rice samosa samosas laddu laddus laddoo laddoos jalebi jalebis paan sweets mithai biryani curry pickle
        ghee butter egg eggs chicken mutton almonds badam sugar salt sherbet beer whisky whiskey rum liquor
        alcohol soda glucose tiffin thali thalis lunchbox""".split(), "prop:food_drink"),
    **dict.fromkeys("cigarette cigarettes cigar cigars bidi bidis beedi beedis hookah pipe tobacco gutka matchbox "
                    "matches lighter".split(), "prop:consumable"),
    **dict.fromkeys("handcuffs handcuff shackles leash".split(), "prop:restraint"),
    **dict.fromkeys("""stretcher stretchers syringe syringes bandage bandages medicine medicines pill pills tablet
        tablets injection injections stethoscope crutch crutches wheelchair wheelchairs thermometer plaster
        sling ointment tonic drip saline""".split(), "prop:medical"),
    **dict.fromkeys("pen pens pencil pencils chalk slate eraser ruler stamp stamps inkpot".split(), "prop:stationery"),
    **dict.fromkeys("""whistle whistles medal medals trophy trophies cup garland garlands flag flags bouquet
        bouquets candle candles lamp lamps lantern lanterns torch torches diya diyas agarbatti incense idol idols
        rakhi umbrella umbrellas mirror mirrors clock clocks watch watches alarm bell bells towel towels
        handkerchief handkerchiefs blanket blankets pillow pillows mattress mattresses sheet sheets rope ropes
        stick sticks broom brooms bucket buckets mug mugs tumbler tumblers lota matka pot pots pan pans tawa
        kite kites ball balls bat bats doll dolls toy toys balloon balloons dumbbell dumbbells barbell barbells
        weights mat mats comb combs scissors clippers razor razors soap toothbrush lipstick sindoor kajal
        ribbon ribbons cot cots charpai charpoy hammer hammers spade spades shovel shovels plough sack sacks
        net nets cage cages ladder ladders tyre tyres tire tires gift gifts parcel parcels package packages
        crate crates tarpaulin glass plate plates lock locks padlock chain chains brick bricks clipboard clipboards
        cable cables battery batteries confetti trunk trunks jar jars cane canes mace gada tumbler tumblers
        monitor monitors cooler stapler printer charger chargers heater mixer cooker ruler rulers
        tweezers shears weights""".split(), "prop:other"),
    **dict.fromkeys("necklace necklaces bangle bangles earring earrings ring rings bracelet bracelets anklet "
                    "anklets payal mangalsutra nath pendant pendants locket lockets jewellery jewelry".split(),
                    "prop:jewellery"),
    # costume
    **dict.fromkeys("""kurta kurtas pyjama pyjamas pajama pajamas salwar kameez sari saris saree sarees lehenga
        lehengas sherwani sherwanis dhoti dhotis lungi lungis ghagra choli burqa burkha hijab achkan angrakha
        langot langots""".split(), "cos:traditional"),
    **dict.fromkeys("""dupatta dupattas pagdi pagri topi turban turbans chappal chappals sandal sandals juti jutis
        jooti jootis shoe shoes boot boots slipper slippers sneaker sneakers sock socks cap caps hat hats helmet
        helmets scarf scarves muffler shawl shawls glove gloves belt belts tie ties spectacles glasses sunglasses
        goggles wristwatch veil mask masks bandana apron""".split(), "cos:accessory"),
    **dict.fromkeys("uniform uniforms khaki fatigues scrubs livery".split(), "cos:uniform"),
    **dict.fromkeys("""tracksuit tracksuits singlet singlets jersey jerseys shorts swimsuit sweatshirt leotard
        kneepads tights""".split(), "cos:sportswear"),
    **dict.fromkeys("nightdress nightgown nightie nightshirt bathrobe".split(), "cos:nightwear"),
    **dict.fromkeys("suit suits tuxedo blazer blazers gown gowns".split(), "cos:formal"),
    # vehicles
    **dict.fromkeys("jeep jeeps".split(), "veh:jeep"),
    **dict.fromkeys("taxi taxis cab cabs".split(), "veh:taxi"),
    **dict.fromkeys("ambulance ambulances".split(), "veh:ambulance"),
    **dict.fromkeys("auto autos autorickshaw autorickshaws auto-rickshaw auto-rickshaws rickshaw rickshaws tempo "
                    "tempos".split(), "veh:auto_rickshaw"),
    **dict.fromkeys("tonga tongas bullock-cart cart carts carriage carriages buggy chariot".split(), "veh:animal_drawn"),
    **dict.fromkeys("scooter scooters scooty moped mopeds bike bikes motorbike motorbikes motorcycle motorcycles "
                    "bullet".split(), "veh:motorcycle"),
    **dict.fromkeys("cycle cycles bicycle bicycles tricycle".split(), "veh:bicycle"),
    **dict.fromkeys("van vans minivan matador".split(), "veh:van"),
    **dict.fromkeys("lorry lorries truck trucks tanker tankers tractor tractors trolley trolleys".split(), "veh:truck"),
    **dict.fromkeys("train trains locomotive tram trams".split(), "veh:train"),
    # animals the dictionary files oddly
    **dict.fromkeys("buffalo buffaloes buffalos".split(), "animal:buffalo"),
    **dict.fromkeys("bullock bullocks ox oxen bull bulls cow cows calf calves cattle".split(), "animal:cow"),
    **dict.fromkeys("hen hens rooster roosters chick chicks chickens cock".split(), "animal:bird"),
    # music
    **dict.fromkeys("dhol dhols dholak tabla sitar shehnai harmonium sarangi veena tanpura bansuri nagada ektara "
                    "manjira bugle bugles drum-kit".split(), "inst"),
}
# The "bullet" above means the Royal Enfield only in "veh" context; as a word it is ammunition.
EXTRA["bullet"] = "prop:weapon"
EXTRA["cup"] = "prop:other"

# Words the dictionary files as a person first but which are things in a script.
NOT_PEOPLE = set("""monitor quarry tumbler cooler speaker receiver transmitter dispenser washer dryer cutter
    trimmer clipper tracker pointer marker sleeper rocker roller shaker lighter stretcher sweater jumper
    conductor carrier feeder charger starter opener holder hanger stapler printer scanner server copier
    burner heater mixer grinder blender toaster cooker freezer fan pilot ruler mouse bat crane mark bill
    pat sue dick john jack jimmy bob will""".split())
# Nouns that look plural but are one object: keep the word as written ("CLIPPERS", not "CLIPPER").
PLURAL_ONLY = set("""clippers scissors pliers tongs trousers pants jeans shorts glasses spectacles sunglasses
    goggles handcuffs shackles binoculars headphones earphones pyjamas pajamas tights kneepads tweezers
    shears bellows dungarees overalls leggings briefs boxers knickers fatigues scrubs weights""".split())

COSTUME_ACCESSORY_ANCHORS = ("footwear", "headdress", "glove", "scarf", "belt", "necktie", "hosiery")


def _plurals(word: str) -> set[str]:
    if " " in word:
        head, _, last = word.rpartition(" ")
        return {f"{head} {p}" for p in _plurals(last)}
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return {word + "es"}
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return {word[:-1] + "ies"}
    if word.endswith("man"):
        return {word[:-3] + "men", word + "s"}
    return {word + "s"}


def _verb_forms(v: str) -> tuple[set[str], set[str]]:
    """(-s forms, other forms) for a verb lemma. Over-generates a little; harmless."""
    if " " in v or not v.isalpha():
        return set(), set()
    s = {v + "es"} if v.endswith(("s", "x", "z", "ch", "sh", "o")) else \
        {v[:-1] + "ies"} if v.endswith("y") and len(v) > 1 and v[-2] not in "aeiou" else {v + "s"}
    other = set()
    if v.endswith("e"):
        other |= {v + "d", v[:-1] + "ing"}
    elif v.endswith("y") and len(v) > 1 and v[-2] not in "aeiou":
        other |= {v[:-1] + "ied", v + "ing"}
    else:
        other |= {v + "ed", v + "ing"}
        if len(v) >= 3 and v[-1] not in "aeiouwxy" and v[-2] in "aeiou" and v[-3] not in "aeiou":
            other |= {v + v[-1] + "ed", v + v[-1] + "ing"}       # grab -> grabbed, grabbing
    return s, other


class WordNet:
    def __init__(self, wn: Path):
        self.syn: dict[str, tuple[int, list[str], str]] = {}
        for line in (wn / "data.noun").read_text(encoding="latin-1").splitlines():
            if not line or line.startswith(" "):
                continue
            head, _, gloss = line.partition("|")
            p = head.split()
            wcnt = int(p[3], 16)
            i = 4 + 2 * wcnt
            pcnt = int(p[i])
            hyper = [p[i + 2 + 4 * k] for k in range(pcnt) if p[i + 1 + 4 * k] in ("@", "@i") and p[i + 3 + 4 * k] == "n"]
            self.syn[p[0]] = (int(p[1]), hyper, gloss.strip().lower())
        self.index: dict[str, list[str]] = {}
        for line in (wn / "index.noun").read_text(encoding="latin-1").splitlines():
            if not line or line.startswith(" "):
                continue
            p = line.split()
            n, pc = int(p[2]), int(p[3])
            self.index[p[0]] = p[6 + pc:6 + pc + n]       # ordered by how common each meaning is
        self._closure: dict[str, frozenset[str]] = {}

    def s(self, word: str, n: int = 1) -> str:
        return self.index[word][n - 1]

    def closure(self, off: str) -> frozenset[str]:
        if off not in self._closure:
            out, stack = {off}, [off]
            while stack:
                for h in self.syn[stack.pop()][1]:
                    if h not in out:
                        out.add(h)
                        stack.append(h)
            self._closure[off] = frozenset(out)
        return self._closure[off]


def _tagger(w: WordNet):
    s = w.s
    veh, wheeled = s("conveyance", 3), s("wheeled_vehicle")
    veh_sub = [("motorcycle", [s("motorcycle"), s("motor_scooter")]), ("bus", [s("bus")]), ("van", [s("van", 5)]),
               ("truck", [s("truck")]), ("taxi", [s("cab", 3)]), ("ambulance", [s("ambulance")]),
               ("jeep", [s("jeep")]), ("car", [s("car")]), ("bicycle", [s("bicycle")]),
               ("boat", [s("vessel", 2)]), ("aircraft", [s("aircraft")]),
               ("train", [s("train"), s("locomotive"), s("car", 2)]),
               ("animal_drawn", [s("horse-drawn_vehicle"), s("cart")])]
    animal = s("animal")
    animal_sub = [("dog", [s("dog")]), ("horse", [s("equine")]), ("buffalo", [s("old_world_buffalo")]),
                  ("cow", [s("bovine")]), ("goat", [s("goat")]), ("cat", [s("feline")]), ("bird", [s("bird")]),
                  ("snake", [s("snake")]), ("elephant", [s("elephant")]), ("monkey", [s("primate", 2)]),
                  ("camel", [s("camel")]), ("fish", [s("fish")]), ("insect", [s("insect"), s("arthropod")])]
    cos = [s("clothing"), s("garment"), s("footwear", 1), s("footwear", 2), s("headdress")]
    cos_acc = [w.index[a][0] for a in COSTUME_ACCESSORY_ANCHORS if a in w.index] + [s("footwear", 2)]
    props = [
        ("weapon", [s("weapon")]),
        ("jewellery", [s("jewelry")]),
        ("bag", [s("bag"), s("bag", 4), s("luggage")]),
        ("medical", [s("medicine", 2), s("medical_instrument"), s("dressing", 6) if len(w.index["dressing"]) > 5 else s("bandage")]),
        ("stationery", [s("writing_implement")]),
        ("tool", [s("tool"), s("hand_tool"), s("rope"), s("edge_tool"), s("cutter", 6) if len(w.index["cutter"]) > 5 else s("tool")]),
        ("electronics", [s("electronic_equipment"), s("computer"), s("camera"), s("photographic_equipment")]),
        ("other", [s("container"), s("implement"), s("tableware"), s("plaything"), s("timepiece"), s("lock"),
                   s("signaling_device"), s("optical_instrument"), s("lamp"), s("candle"), s("mirror"),
                   s("sports_equipment"), s("game_equipment"), s("piece_of_cloth"), s("bedclothes"), s("toiletry"),
                   s("trophy", 2), s("kitchen_utensil"), s("cooking_utensil")]),
    ]
    inst = s("musical_instrument")

    def tag(off: str) -> str:
        lexf, _, _ = w.syn[off]
        if lexf == BODY:
            return "body"
        if lexf == FOOD:
            return "prop:food_drink"
        cl = w.closure(off)
        if lexf == ANIMAL:
            if animal not in cl:
                return ""                           # a beak or a paw, not an animal
            return "animal:" + next((name for name, anchors in animal_sub if any(a in cl for a in anchors)), "other")
        if lexf != ARTIFACT:
            return ""
        if veh in cl:
            sub = next((name for name, anchors in veh_sub if any(a in cl for a in anchors)), None)
            if sub:
                return "veh:" + sub
            return "prop:other" if wheeled in cl else ""       # a pram or a wheelbarrow is a prop
        if inst in cl:
            return "inst"
        if any(c in cl for c in cos):
            return "cos:accessory" if any(a in cl for a in cos_acc) else "cos:everyday"
        for name, anchors in props:
            if any(a in cl for a in anchors):
                return "prop:" + name
        return ""
    return tag


def build(wn: Path) -> dict[str, list[str]]:
    w = WordNet(wn)
    tag_of = _tagger(w)

    def code_for(offsets: list[str]) -> str:
        first, _, gloss = w.syn[offsets[0]]
        if first == PERSON:
            return "P"
        if first == GROUP and any(h in gloss for h in PEOPLE_GROUP_HINTS):
            return "G"
        if first == ANIMAL:
            return "A"
        if any(w.syn[o][0] == PERSON for o in offsets[1:]):
            return "p"
        return "O"

    # form -> [code, tag, lemma, pos]
    rows: dict[str, list[str]] = {}
    nouns: dict[str, tuple[str, str]] = {}
    for lemma, offsets in w.index.items():
        word = lemma.replace("_", " ")
        if not word.replace(" ", "").replace("-", "").replace("'", "").isalpha():
            continue
        code = code_for(offsets)
        tag = "" if (word in STOP or code in ("P", "G")) else tag_of(offsets[0])
        nouns[word] = (code, tag)
        rows[word] = [code, tag, "", "n"]
    exc = {}
    for line in (wn / "noun.exc").read_text(encoding="latin-1").splitlines():
        plural, *bases = line.split()
        if bases:
            exc[plural.replace("_", " ")] = bases[0].replace("_", " ")
    for plural, base in exc.items():
        if base in nouns and plural not in nouns:
            rows[plural] = [*nouns[base], base, "n"]
    for word, (code, tag) in nouns.items():
        for pl in _plurals(word):
            if pl not in rows:
                rows[pl] = [code, tag, word, "n"]
            elif not rows[pl][2] and pl.endswith("s") and pl != word and rows[pl][1] == "":
                rows[pl][2] = word              # "eyes" is its own word, and also the plural of "eye"
                if rows[pl][0] not in ("P", "G"):
                    rows[pl][1] = tag

    def mark(form: str, letter: str) -> None:
        if not form.replace("-", "").replace("'", "").replace(" ", "").isalpha():
            return
        r = rows.setdefault(form, ["", "", "", ""])
        if letter not in r[3]:
            r[3] += letter

    for line in (wn / "index.verb").read_text(encoding="latin-1").splitlines():
        if line and not line.startswith(" "):
            v = line.split()[0].replace("_", " ")
            mark(v, "v")
            third, other = _verb_forms(v)
            for f in third:
                mark(f, "z")
            for f in other:
                mark(f, "w")
    for line in (wn / "verb.exc").read_text(encoding="latin-1").splitlines():
        form = line.split()[0]
        mark(form, "z" if form.endswith("s") and not form.endswith("ss") else "w")
    for fname, letter in (("index.adj", "a"), ("index.adv", "r")):
        for line in (wn / fname).read_text(encoding="latin-1").splitlines():
            if line and not line.startswith(" "):
                mark(line.split()[0].replace("_", " "), letter)
    for fname, letter in (("adj.exc", "a"), ("adv.exc", "r")):
        for line in (wn / fname).read_text(encoding="latin-1").splitlines():
            if line.strip():
                mark(line.split()[0].replace("_", " "), letter)

    # Hand-checked words win over the dictionary.
    for form, tag in EXTRA.items():
        r = rows.setdefault(form, ["O", "", "", "n"])
        r[0] = "A" if tag.startswith("animal") else "O"       # a hand-checked thing is never a person
        if "n" not in r[3]:
            r[3] += "n"
        r[1] = tag
    rows["fans"] = ["P", "", "fan", "n"]          # "thousands of fans" are people; one "fan" is the appliance
    for form in NOT_PEOPLE:
        for f in (form, *(_plurals(form))):
            if f in rows and rows[f][0] in ("P", "p"):
                rows[f][0] = "O"
    for form in PLURAL_ONLY:
        if form in rows:
            rows[form][2] = ""
    # A plural takes the hand-checked tag of its singular ("medals" -> medal).
    for form, r in rows.items():
        if r[2] and r[2] in EXTRA and form not in EXTRA and r[0] not in ("P", "G"):
            r[1] = EXTRA[r[2]]
    for form in STOP:
        if form in rows and form not in EXTRA:
            rows[form][1] = ""
    return rows


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    wn = Path(sys.argv[1])
    rows = build(wn)
    OUT.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{f}\t" + "\t".join(r) + "\n" for f, r in sorted(rows.items()))
    with gzip.GzipFile(OUT / "words.tsv.gz", "wb", mtime=0) as f:
        f.write(body.encode("utf-8"))
    shutil.copy(wn / "LICENSE", OUT / "WORDNET_LICENSE.txt")
    counts: dict[str, int] = {}
    for r in rows.values():
        k = r[1].split(":")[0] or "-"
        counts[k] = counts.get(k, 0) + 1
    print(f"{len(rows)} forms written to {OUT / 'words.tsv.gz'}; tags: {counts}")


if __name__ == "__main__":
    main()
