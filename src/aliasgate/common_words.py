"""Common English and Spanish words: a person's name word on this list is replaced only where
it starts with an upper-case letter (architecture §4.3, step 2b)."""

from typing import Final

from aliasgate.mapping import normalize_key

# English closed-class and courtesy words.
_ENGLISH_CLOSED: Final = """
a about above across after again against all almost along also although always am among an
and another any anybody anyone anything are around as at away back be because been before
behind being below beside besides between beyond both but by bye can cannot cheers could
dear did do does doing don done down during each either else enough even ever every
everybody everyone everything few for from further get gets got had has have having he
hello her here hers herself hey hi him himself his how however i if in into is it its
itself just least less like many may me might mine more most much must my myself near need
neither never next no nobody none nor not nothing now of off often oh ok okay on once one
only onto or other others ought our ours ourselves out over own per perhaps please quite
rather regards same shall she should since so some somebody someone something soon sorry
still such than thank thanks that the their theirs them themselves then there these they
this those though through thus till to together too toward towards under unless until up
upon us very via was we well were what whatever when where whether which while who whom
whose why will with within without won would yeah yes yet you your yours yourself
yourselves
"""

# English common words that are also names.
_ENGLISH_NAMES: Final = """
amber april art august banks bell best bill black bond booth brown bush case cash chance
chase church cook cross crystal daisy dawn day dean duke earl east faith field fields fox
frank frost gold grace gray green grey guy hall heather holly honor hope hunt iris ivy
jack jade joy june king knight lake lamb lane lily little long major mark miles moon north
page park parks pat pearl penny post power price prince ray rice rich rob rock rose ruby
rush sandy sharp short silver snow south sterling stone storm street strong sue summer
swan violet wall ward waters wells west white winter wise wolf wood woods young
"""

# Spanish closed-class and courtesy words, in Spanish alphabetical order.
_SPANISH_CLOSED: Final = """
a adiós ahí ahora al algo alguien algún alguna algunas alguno algunos allí ante antes
aquel aquella aquellas aquello aquellos aquí así aun aún bajo bien buenas bueno buenos
cada casi como cómo con contra cual cuál cuales cuando cuándo cuanto cuánto de del desde
don donde dónde doña durante e el él ella ellas ello ellos en entre era eran eres es esa
esas ese eso esos esta está estaba estado están estar este esto estos fue fueron gracias
ha había han hasta hay he hemos hola hoy la las le les lo los luego mañana más me mi mí
mis misma mismo mucha mucho muy nada ni ningún ninguna ninguno no nos nosotras nosotros
nuestra nuestras nuestro nuestros o os otra otras otro otros oye para perdón pero poco por
porque pues que qué quien quién quienes saludos se sea según ser si sí siempre sin sino
sobre son su sus también tan tanto te ti tiene tienen toda todas todo todos tras tu tú tus
un una unas uno unos usted ustedes vale vosotras vosotros vuestra vuestro y ya yo
"""

# Spanish common words that are also names, in Spanish alphabetical order.
_SPANISH_NAMES: Final = """
águila alba alegre amado amparo ángel ángeles asunción aurora barba benigno blanca blanco
bravo bueno caballero calvo campo campos cano castaño castillo celeste clara clemente
concepción conde constante consuelo cordero cortés crespo cruz delgado dolores domingo
duque esperanza estrella feliz flores franco fuente fuentes gallego gallo guerra hermoso
herrero iglesia iglesias inmaculada jesús justo leal león llano lobo luna luz mar marqués
mesa milagros modesto montes mora moreno nieto nieves olmo palacio palacios paloma pardo
pastor pastora paz peña piedra pilar pino plácido plaza pozo prado primo pura puerta ramos
remedios rey reyes rico río ríos robles roca rocío rojo romero rosa rosario rubio salvador
santos serrano severo sierra sobrino socorro sol soledad toro torre torres valle vega
victoria
"""

COMMON_WORDS: Final[frozenset[str]] = frozenset(
    normalize_key(word)
    for group in (_ENGLISH_CLOSED, _ENGLISH_NAMES, _SPANISH_CLOSED, _SPANISH_NAMES)
    for word in group.split()
)
"""Keys of English and Spanish words whose lower-case use is common: a name token on this list
is replaced only where it starts with an upper-case letter."""
