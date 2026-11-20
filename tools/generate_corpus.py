"""Generate the synthetic evaluation corpora in tests/eval/corpus/.

Deterministic: the fixed seeds below produce byte-identical files for a given Faker version.
Every value is synthetic: names, companies and addresses come from Faker, identifiers with a
checksum are random digits completed with the checksum functions of aliasgate, emails use
example.com and example.org, IP addresses come from documentation and private ranges, US and
UK phone numbers come from the ranges reserved for fiction, and Spanish ones are random valid
numbers.

    uv run python tools/generate_corpus.py           # write the corpus files
    uv run python tools/generate_corpus.py --check   # exit 1 if the committed files differ
"""

import argparse
import json
import random
import re
import string
import sys
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

import phonenumbers
from faker import Faker

from aliasgate.detectors.checksums import (
    es_cif_valid,
    es_dni_valid,
    es_nie_valid,
    iban_valid,
    luhn_valid,
    us_ssn_plausible,
)

DEFAULT_OUT: Final = Path(__file__).resolve().parent.parent / "tests" / "eval" / "corpus"
LANGUAGES: Final = {"en": ("en_US", 1101), "es": ("es_ES", 1102)}  # locale, seed
EXAMPLES: Final = 500
EMPTY_EXAMPLES: Final = 85  # 17% of the examples contain no entity

_SLOT: Final = re.compile(r"\{\{|\}\}|\{([a-z_]+?)(?:_(\d))?(?:\|(upper|lower))?\}")

# Slot kinds whose values are gold spans, and their entity types.
GOLD_TYPES: Final = {
    "person": "PERSON",
    "org": "ORG",
    "address": "ADDRESS",
    "email": "EMAIL",
    "phone": "PHONE",
    "iban": "IBAN",
    "card": "CREDIT_CARD",
    "national_id": "NATIONAL_ID",
    "tax_id": "TAX_ID",
    "ip": "IP_ADDRESS",
}


# Templates -------------------------------------------------------------------------------------
# Slots: {kind} or {kind_2} for a second, different value of the same kind; {kind|upper} and
# {kind|lower} repeat the value of {kind} in another letter case. {{ and }} are literal braces.

EN_TEMPLATES: Final = (
    # support tickets
    "Ticket {ticket}: {person} ({email}) cannot log in since {date}. Last successful login "
    "came from {ip}.",
    "Hi team, customer {person} called from {phone} about invoice {order}. Please refund "
    "{amount} to IBAN {iban}.",
    "Escalation for {org}: the account owner, {person}, asked us to remove the card {card} "
    "from file.",
    "Customer reports a duplicate charge of {amount} on card {card} on {date}. Contact: "
    "{email}, {phone}.",
    "Priority P2 - {org} - user {email} gets HTTP 500 since version {version}; client IP {ip}.",
    "Refund approved for {person}. Note: {person|upper} must confirm the IBAN {iban} by email "
    "at {email}.",
    # emails
    "From: {person} <{email}>\nTo: {email_2}\nSubject: Updated bank details\n\nPlease use "
    "{iban} for all future payments from {org}.\n\nRegards,\n{person}",
    "Dear {person},\n\nThank you for contacting {org}. Your request {ticket} has been "
    "registered and we will call you at {phone} within 48 hours.\n\nKind regards",
    "Hello {person}, your new card ending in {last4} has been sent to {address}.",
    "Please update the delivery address for order {order} to {address}. Contact person: "
    "{person}, phone {phone}.",
    "Email bounced for {email}; the customer wrote it as {email|upper} on the paper form.",
    # contract clauses
    "This agreement is entered into on {date} between {org}, with tax ID {tax_id}, and "
    "{person}, holder of ID {national_id}, residing at {address}.",
    "Payments shall be made by bank transfer to the account {iban} held by {org} no later "
    "than {qty} days after invoicing.",
    'The Supplier, {org} (hereinafter "{org|upper}"), shall notify the Client in writing at '
    "{email}.",
    "Notices to the Customer shall be sent to {person}, {address}, or by email to {email}.",
    "Signed on behalf of {org} by {person}, ID number {national_id}, on {date}.",
    "Clause {qty}. {org} (VAT {tax_id}) guarantees the confidentiality of the data of {person}.",
    # CRM notes
    "Met {person} from {org} on {date}. Interested in the premium plan ({amount}/month). "
    "NOTE: {person|upper} prefers calls at {phone}.",
    "Lead: {person} | Company: {org} | Phone: {phone} | Email: {email} | Source: webinar {date}",
    "Call notes: {person} confirmed the renewal for {org}. Billing contact is {person_2} "
    "({email}).",
    "Account {org} flagged: card {card} declined twice, the customer asked to switch to SEPA "
    "debit from {iban}.",
    "{person} ({national_id}) visited the branch and updated the phone number to {phone}.",
    "{org} has renewed for {year}. {org|lower} contact for invoices: {email}.",
    # chat messages
    "hey, can you send the contract to {email}? thx",
    "my iban is {iban}, can you check why the payment of {amount} failed",
    "ok so the server at {ip} is down again, {person} is looking into it",
    "call me at {phone} when you can, it's about the {org} order",
    "is {person|lower} the same as {person}? the CRM shows two records",
    "sure, my email is {email} and my ID is {national_id}",
    "Transfer done from {iban}. The bank app shows it as {iban|lower}, weird",
    # log lines
    "{timestamp} INFO auth: login succeeded user={email} ip={ip}",
    "{timestamp} WARN payments: card {card} rejected (code 05) for merchant {org}",
    "{timestamp} ERROR api: request {ticket} from {ip} failed after {qty} ms",
    "[{timestamp}] user {email} changed iban to {iban}",
    '{timestamp} DEBUG crm: contact updated name="{person}" phone="{phone}"',
    "{timestamp} INFO kyc: document {national_id} verified for {person}; {national_id|lower} "
    "cached",
    # CSV-like rows
    "id,name,email,phone\n{qty},{person},{email},{phone}",
    "customer;iban;card\n{org};{iban};{card}",
    '"{person}","{address}","{national_id}","{date}"',
    "{order}\t{org}\t{tax_id}\t{amount}",
    # JSON snippets
    '{{"name": "{person}", "email": "{email}", "phone": "{phone}"}}',
    '{{"customer": {{"company": "{org}", "vat": "{tax_id}"}}, "iban": "{iban}"}}',
    '{{"event": "login", "ip": "{ip}", "user": "{email}", "ts": "{timestamp}"}}',
    '{{"cardholder": "{person}", "pan": "{card}", "billing_address": "{address}"}}',
    # mixed
    "Wire from {org} ({tax_id}) to {iban}, reference {order}, amount {amount}.",
    "Server {ip} and its backup {ip_2} both reported disk errors on {date}.",
    "Forward the invoice for {org} to {person} at {address}.",
)

EN_EMPTY_TEMPLATES: Final = (
    "Order {order} shipped on {date}; total {amount} including VAT.",
    "Release {version} fixes the timeout bug reported in {year}. Upgrading from {version_2} "
    "is recommended.",
    "The book's ISBN is {isbn}, published in {year}, {qty} pages.",
    "Meeting moved to {date} at {time}, room {room}.",
    "Invoice {order} for {amount} is due within {qty} days.",
    "{timestamp} INFO scheduler: job {ticket} finished in {qty} ms, {percent} CPU",
    "sku,qty,price\n{sku},{qty},{amount}",
    '{{"order": "{order}", "total": "{amount}", "date": "{date}"}}',
    "We grew revenue by {percent} between {year} and {year_2}.",
    "Please quote reference {order} and ISBN {isbn} in your reply.",
    "Build {version} passed {qty} of {qty_2} tests on {date}.",
    "The parcel weighs {qty} kg and costs {amount} to ship; tracking {tracking}.",
    "Chapter {qty}: sales figures for Q{quarter} {year}.",
    "lol the meeting is at {time}, bring the {year} report",
    "Stock check {date}: {qty} units of {sku} left, reorder at {qty_2}.",
)

ES_TEMPLATES: Final = (
    # incidencias de soporte
    "Incidencia {ticket}: {person} ({email}) no puede acceder desde el {date}. El último "
    "acceso fue desde {ip}.",
    "Hola equipo, el cliente {person} ha llamado desde el {phone} por la factura {order}. "
    "Devolved {amount} a la cuenta {iban}.",
    "Escalado para {org}: el titular de la cuenta, {person}, pide que borremos la tarjeta {card}.",
    "El cliente reclama un cargo duplicado de {amount} en la tarjeta {card} el {date}. "
    "Contacto: {email}, {phone}.",
    "Prioridad alta - {org} - el usuario {email} recibe un error 500 desde la versión "
    "{version}; IP {ip}.",
    "Devolución aprobada para {person}. Ojo: {person|upper} debe confirmar el IBAN {iban} "
    "por correo en {email}.",
    # correos
    "De: {person} <{email}>\nPara: {email_2}\nAsunto: Nuevos datos bancarios\n\nA partir de "
    "ahora usad la cuenta {iban} para los pagos de {org}.\n\nUn saludo,\n{person}",
    "Estimado/a {person}:\n\nGracias por contactar con {org}. Su solicitud {ticket} ha quedado "
    "registrada y le llamaremos al {phone} en un plazo de 48 horas.\n\nAtentamente",
    "Hola {person}, tu nueva tarjeta terminada en {last4} se ha enviado a {address}.",
    "Por favor, cambiad la dirección de entrega del pedido {order} a {address}. Persona de "
    "contacto: {person}, teléfono {phone}.",
    "El correo a {email} ha rebotado; en el formulario en papel lo escribió como {email|upper}.",
    # cláusulas de contrato
    "En {city}, a {date}, REUNIDOS: de una parte, {org}, con CIF {tax_id}, y de otra, "
    "{person}, con DNI {national_id} y domicilio en {address}.",
    "Los pagos se realizarán por transferencia a la cuenta {iban}, de la que es titular "
    "{org}, en un plazo máximo de {qty} días desde la factura.",
    'El Proveedor, {org} (en adelante, "{org|upper}"), notificará al Cliente por escrito en '
    "{email}.",
    "Las notificaciones al Cliente se enviarán a {person}, {address}, o por correo "
    "electrónico a {email}.",
    "Firmado en nombre de {org} por {person}, con documento {national_id}, el {date}.",
    "Cláusula {qty}. {org} (CIF {tax_id}) garantiza la confidencialidad de los datos de {person}.",
    # notas de CRM
    "Reunión con {person} de {org} el {date}. Le interesa el plan premium ({amount} al mes). "
    "NOTA: {person|upper} prefiere que le llamen al {phone}.",
    "Lead: {person} | Empresa: {org} | Teléfono: {phone} | Email: {email} | Origen: webinar {date}",
    "Notas de la llamada: {person} confirma la renovación de {org}. El contacto de "
    "facturación es {person_2} ({email}).",
    "Cuenta {org} marcada: la tarjeta {card} se ha rechazado dos veces y el cliente pide "
    "domiciliar en {iban}.",
    "{person} ({national_id}) pasó por la oficina y cambió su teléfono al {phone}.",
    "{org} ha renovado para {year}. Contacto de {org|lower} para facturas: {email}.",
    # mensajes de chat
    "oye, ¿me mandas el contrato a {email}? gracias",
    "mi iban es {iban}, ¿puedes mirar por qué ha fallado el pago de {amount}?",
    "vale, el servidor {ip} se ha vuelto a caer, {person} lo está mirando",
    "llámame al {phone} cuando puedas, es por el pedido de {org}",
    "¿{person|lower} es la misma persona que {person}? en el CRM salen dos fichas",
    "claro, mi correo es {email} y mi DNI es {national_id}",
    "Transferencia hecha desde {iban}. En la app del banco sale como {iban|lower}, qué raro",
    # líneas de log
    "{timestamp} INFO auth: inicio de sesión correcto usuario={email} ip={ip}",
    "{timestamp} WARN pagos: tarjeta {card} rechazada (código 05) para el comercio {org}",
    "{timestamp} ERROR api: la petición {ticket} desde {ip} falló tras {qty} ms",
    "[{timestamp}] el usuario {email} cambió el iban a {iban}",
    '{timestamp} DEBUG crm: contacto actualizado nombre="{person}" telefono="{phone}"',
    "{timestamp} INFO kyc: documento {national_id} verificado para {person}; "
    "{national_id|lower} en caché",
    # filas tipo CSV
    "id;nombre;email;telefono\n{qty};{person};{email};{phone}",
    "cliente;iban;tarjeta\n{org};{iban};{card}",
    '"{person}";"{address}";"{national_id}";"{date}"',
    "{order}\t{org}\t{tax_id}\t{amount}",
    # fragmentos JSON
    '{{"nombre": "{person}", "email": "{email}", "telefono": "{phone}"}}',
    '{{"cliente": {{"empresa": "{org}", "cif": "{tax_id}"}}, "iban": "{iban}"}}',
    '{{"evento": "login", "ip": "{ip}", "usuario": "{email}", "ts": "{timestamp}"}}',
    '{{"titular": "{person}", "pan": "{card}", "direccion_facturacion": "{address}"}}',
    # mezcla
    "Transferencia de {org} ({tax_id}) a {iban}, concepto {order}, importe {amount}.",
    "El servidor {ip} y su réplica {ip_2} dieron errores de disco el {date}.",
    "Reenvía la factura de {org} a {person}, {address}.",
)

ES_EMPTY_TEMPLATES: Final = (
    "El pedido {order} salió el {date}; total {amount} con IVA.",
    "La versión {version} corrige el error de tiempo de espera de {year}. Se recomienda "
    "actualizar desde la {version_2}.",
    "El ISBN del libro es {isbn}, publicado en {year}, {qty} páginas.",
    "La reunión pasa al {date} a las {time}, sala {room}.",
    "La factura {order} de {amount} vence en {qty} días.",
    "{timestamp} INFO planificador: la tarea {ticket} terminó en {qty} ms, {percent} de CPU",
    "sku;cantidad;precio\n{sku};{qty};{amount}",
    '{{"pedido": "{order}", "total": "{amount}", "fecha": "{date}"}}',
    "Los ingresos crecieron un {percent} entre {year} y {year_2}.",
    "Indique la referencia {order} y el ISBN {isbn} en su respuesta.",
    "La build {version} pasó {qty} de {qty_2} pruebas el {date}.",
    "El paquete pesa {qty} kg y el envío cuesta {amount}; seguimiento {tracking}.",
    "Capítulo {qty}: ventas del T{quarter} de {year}.",
    "jaja la reunión es a las {time}, trae el informe de {year}",
    "Inventario del {date}: quedan {qty} unidades de {sku}, pedir más al llegar a {qty_2}.",
)

TEMPLATES: Final = {
    "en": (EN_TEMPLATES, EN_EMPTY_TEMPLATES),
    "es": (ES_TEMPLATES, ES_EMPTY_TEMPLATES),
}

# Values ----------------------------------------------------------------------------------------

_MONTHS: Final = {
    "en": (
        "January", "February", "March", "April", "May", "June", "July", "August",
        "September", "October", "November", "December",
    ),
    "es": (
        "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
        "septiembre", "octubre", "noviembre", "diciembre",
    ),
}  # fmt: skip
_EMAIL_DOMAINS: Final = ("example.com", "example.org", "mail.example.com", "corp.example.org")
_CIF_LETTERS: Final = "ABCDEFGHJNPQRSUVW"
# IBAN country, check digits placeholder, BBAN layout: "n" digit, "a" upper-case letter.
_IBAN_LAYOUTS: Final = {"ES": "n" * 20, "DE": "n" * 18, "GB": "aaaa" + "n" * 14, "FR": "n" * 23}


def _ascii(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if char in string.ascii_letters)


class _Values:
    """Random slot values for one language, all drawn from one seeded Faker instance."""

    def __init__(self, fake: Faker, lang: str) -> None:
        self._fake = fake
        self._lang = lang
        self.rng: random.Random = fake.random

    def _chance(self, probability: float) -> bool:
        return self.rng.random() < probability

    def _digits(self, count: int) -> str:
        return "".join(self.rng.choice(string.digits) for _ in range(count))

    def _octet(self) -> int:
        return self.rng.randrange(256)

    def _case(self, value: str, lower: float) -> str:
        return value.lower() if self._chance(lower) else value

    # gold values

    def person(self) -> str:
        fake = self._fake
        if self._lang == "es" and self._chance(0.6):
            return f"{fake.first_name()} {fake.last_name()} {fake.last_name()}"
        return f"{fake.first_name()} {fake.last_name()}"

    def org(self) -> str:
        return " ".join(self._fake.company().split())

    def address(self) -> str:
        lines = (line.strip() for line in self._fake.address().splitlines())
        return ", ".join(line for line in lines if line)

    def email(self) -> str:
        fake = self._fake
        style = self.rng.randrange(4)
        if style == 0:
            local = f"{_ascii(fake.first_name())}.{_ascii(fake.last_name())}".lower()
        elif style == 1:
            local = f"{_ascii(fake.first_name())[0]}{_ascii(fake.last_name())}".lower()
        elif style == 2:
            local = f"{fake.user_name()}+{self.rng.choice(('billing', 'crm', 'news'))}"
        else:
            local = fake.user_name()
        address = f"{local}@{self.rng.choice(_EMAIL_DOMAINS)}"
        return address.capitalize() if self._chance(0.1) else address

    def phone(self) -> str:
        if self._chance(0.1):
            # London numbers reserved by Ofcom for fiction, in international format.
            return f"+44 20 7946 0{self._digits(3)}"
        if self._lang == "en":
            number = self._us_phone()
            formats = ("(%s) %s-%s", "%s-%s-%s", "%s.%s.%s", "+1 %s-%s-%s", "+1%s%s%s")
            digits = phonenumbers.national_significant_number(number)
            return self.rng.choice(formats) % (digits[:3], digits[3:6], digits[6:])
        number = self._es_phone()
        digits = phonenumbers.national_significant_number(number)
        style = self.rng.randrange(5)
        if style == 0:
            return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.NATIONAL)
        if style == 1:
            return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        if style == 2:
            return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
        if style == 3:
            return f"{digits[:3]} {digits[3:6]} {digits[6:]}"
        return digits

    def _us_phone(self) -> phonenumbers.PhoneNumber:
        # 555-0100 to 555-0199 are reserved for fiction in every area code.
        while True:
            area = self._fake.numerify("%##")
            number = phonenumbers.parse(f"{area}5550{self.rng.randrange(100, 200)}", "US")
            if phonenumbers.is_valid_number(number):
                return number

    def _es_phone(self) -> phonenumbers.PhoneNumber:
        while True:
            number = phonenumbers.parse(self._fake.phone_number(), "ES")
            if phonenumbers.is_valid_number(number):
                return number

    def iban(self) -> str:
        country = self.rng.choice(tuple(_IBAN_LAYOUTS))
        bban = "".join(
            self.rng.choice(string.digits if kind == "n" else string.ascii_uppercase)
            for kind in _IBAN_LAYOUTS[country]
        )
        iban = next(
            candidate
            for check in range(2, 99)
            if iban_valid(candidate := f"{country}{check:02d}{bban}")
        )
        if self._chance(0.6):
            iban = " ".join(iban[index : index + 4] for index in range(0, len(iban), 4))
        return self._case(iban, lower=0.15)

    def card(self) -> str:
        kind = self.rng.randrange(3)
        groups: tuple[int, ...]
        if kind == 2:
            body = self.rng.choice(("34", "37")) + self._digits(12)
            groups = (4, 6, 5)
        else:
            body = ("4" if kind == 0 else str(self.rng.randrange(51, 56))) + self._digits(14)
            body = body[:15]
            groups = (4, 4, 4, 4)
        number = next(c for digit in string.digits if luhn_valid(c := body + digit))
        separator = self.rng.choice((" ", "-", ""))
        parts, position = [], 0
        for size in groups:
            parts.append(number[position : position + size])
            position += size
        return separator.join(parts)

    def national_id(self) -> str:
        if self._lang == "en" and self._chance(0.7):
            while True:
                ssn = f"{self.rng.randrange(1, 900):03d}-{self._digits(2)}-{self._digits(4)}"
                if us_ssn_plausible(ssn):
                    return ssn
        separator = "-" if self._chance(0.3) else ""
        if self._chance(0.6):
            digits = self._digits(8)
            letter = next(c for c in string.ascii_uppercase if es_dni_valid(digits + c))
            value = f"{digits}{separator}{letter}"
        else:
            prefix, digits = self.rng.choice("XYZ"), self._digits(7)
            letter = next(c for c in string.ascii_uppercase if es_nie_valid(prefix + digits + c))
            value = f"{prefix}{separator}{digits}{separator}{letter}"
        return self._case(value, lower=0.15)

    def tax_id(self) -> str:
        letter, digits = self.rng.choice(_CIF_LETTERS), self._digits(7)
        controls = [c for c in string.digits + "ABCDEFGHIJ" if es_cif_valid(letter + digits + c)]
        return self._case(letter + digits + self.rng.choice(controls), lower=0.1)

    def ip(self) -> str:
        kind = self.rng.randrange(4)
        if kind == 0:
            network = self.rng.choice(("192.0.2", "198.51.100", "203.0.113"))
            return f"{network}.{self.rng.randrange(1, 255)}"
        if kind == 1:
            return f"10.{self._octet()}.{self._octet()}.{self.rng.randrange(1, 255)}"
        if kind == 2:
            return f"192.168.{self._octet()}.{self.rng.randrange(1, 255)}"
        groups = [f"{self.rng.randrange(1, 0x10000):x}" for _ in range(self.rng.choice((2, 4)))]
        return "2001:db8::" + ":".join(groups)

    # filler values, never gold

    def date(self) -> str:
        day, month = self.rng.randrange(1, 29), self.rng.randrange(1, 13)
        year = self.rng.randrange(2018, 2027)
        style = self.rng.randrange(3)
        if style == 0:
            return f"{year}-{month:02d}-{day:02d}"
        if style == 1:
            first, second = (month, day) if self._lang == "en" else (day, month)
            return f"{first:02d}/{second:02d}/{year}"
        name = _MONTHS[self._lang][month - 1]
        return f"{name} {day}, {year}" if self._lang == "en" else f"{day} de {name} de {year}"

    def timestamp(self) -> str:
        return (
            f"{self.year()}-{self.rng.randrange(1, 13):02d}-{self.rng.randrange(1, 29):02d}"
            f"T{self.rng.randrange(24):02d}:{self.rng.randrange(60):02d}:"
            f"{self.rng.randrange(60):02d}Z"
        )

    def year(self) -> str:
        return str(self.rng.randrange(1990, 2027))

    def time(self) -> str:
        return f"{self.rng.randrange(8, 20)}:{self.rng.choice(('00', '15', '30', '45'))}"

    def amount(self) -> str:
        units, cents = self.rng.randrange(1, 20000), self.rng.randrange(100)
        if self._lang == "en":
            return self.rng.choice(("${:,}.{:02d}", "USD {:,}.{:02d}", "€{:,}.{:02d}")).format(
                units, cents
            )
        grouped = f"{units:,}".replace(",", ".")
        return self.rng.choice(("{} €", "{},{:02d} €", "EUR {},{:02d}")).format(grouped, cents)

    def order(self) -> str:
        prefix = self.rng.choice(
            ("ORD", "INV", "PO") if self._lang == "en" else ("PED", "FAC", "ALB")
        )
        style = self.rng.randrange(3)
        if style == 0:
            return f"{prefix}-{self.year()}-{self._digits(6)}"
        if style == 1:
            return f"#{self.rng.randrange(10000, 99999)}"
        return f"{prefix}{self._digits(7)}"

    def ticket(self) -> str:
        return self.rng.choice(("TCK", "REQ", "INC")) + f"-{self.rng.randrange(1000, 99999)}"

    def version(self) -> str:
        return (
            self.rng.choice(("v", ""))
            + f"{self.rng.randrange(1, 12)}.{self.rng.randrange(30)}.{self.rng.randrange(40)}"
        )

    def isbn(self) -> str:
        body = "978" + self.rng.choice(("84", "0", "3", "1")) + self._digits(12)
        body = body[:12]
        total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
        digits = body + str((10 - total % 10) % 10)
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:8]}-{digits[8:12]}-{digits[12]}"

    def qty(self) -> str:
        return str(self.rng.randrange(2, 500))

    def percent(self) -> str:
        return f"{self.rng.randrange(1, 100)}%"

    def room(self) -> str:
        floor, number = self.rng.randrange(1, 6), self.rng.randrange(1, 40)
        return f"{self.rng.choice('ABCD')}{floor}.{number:02d}"

    def sku(self) -> str:
        return f"SKU-{self.rng.randrange(1000, 9999)}-{self.rng.choice('ABCDEFGH')}"

    def tracking(self) -> str:
        letters = "".join(self.rng.choice(string.ascii_uppercase) for _ in range(2))
        return f"1Z{self._digits(3)}{letters}{self._digits(10)}"

    def quarter(self) -> str:
        return str(self.rng.randrange(1, 5))

    def last4(self) -> str:
        return self._digits(4)

    def city(self) -> str:
        return self._fake.city()


def _other_case(value: str, wanted: str) -> str:
    """`value` in the wanted case, or in the opposite one if that would not change it."""
    first, second = (str.upper, str.lower) if wanted == "upper" else (str.lower, str.upper)
    return first(value) if first(value) != value else second(value)


def render(template: str, values: _Values) -> tuple[str, list[tuple[int, int, str]]]:
    """Fill one template; return the text and its gold spans (start, end, type) in order."""
    slots = [match for match in _SLOT.finditer(template) if match[1] is not None]
    filled: dict[tuple[str, str], str] = {}
    for match in slots:  # values first, in order, so a case variant may precede its value
        key = (match[1], match[2] or "1")
        if match[3] is None and key not in filled:
            generate: Callable[[], str] = getattr(values, match[1])
            filled[key] = generate()
    pieces: list[str] = []
    spans: list[tuple[int, int, str]] = []
    length = position = 0
    for match in _SLOT.finditer(template):
        pieces.append(template[position : match.start()])
        length += match.start() - position
        position = match.end()
        if match[1] is None:  # an escaped brace
            value = match[0][0]
        else:
            value = filled[(match[1], match[2] or "1")]
            if match[3] is not None:
                value = _other_case(value, match[3])
            if match[1] in GOLD_TYPES:
                spans.append((length, length + len(value), GOLD_TYPES[match[1]]))
        pieces.append(value)
        length += len(value)
    pieces.append(template[position:])
    return "".join(pieces), spans


def values_for(lang: str) -> _Values:
    """A fresh value source for one language, seeded with its fixed seed."""
    locale, seed = LANGUAGES[lang]
    fake = Faker(locale)
    fake.seed_instance(seed)
    return _Values(fake, lang)


def generate(lang: str) -> str:
    """The complete JSON Lines content of one language's corpus."""
    values = values_for(lang)
    entity_templates, empty_templates = TEMPLATES[lang]
    rng = values.rng

    def schedule(templates: Sequence[str], count: int) -> list[str]:
        order = list(templates)
        rng.shuffle(order)
        return [order[index % len(order)] for index in range(count)]

    plan = schedule(entity_templates, EXAMPLES - EMPTY_EXAMPLES) + schedule(
        empty_templates, EMPTY_EXAMPLES
    )
    rng.shuffle(plan)
    lines = []
    for number, template in enumerate(plan, start=1):
        text, spans = render(template, values)
        example = {
            "id": f"{lang}-{number:04d}",
            "lang": lang,
            "text": text,
            "spans": [{"start": start, "end": end, "type": kind} for start, end, kind in spans],
        }
        lines.append(json.dumps(example, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Write the corpora, or with --check report whether the committed ones differ."""
    parser = argparse.ArgumentParser(description="Generate the synthetic evaluation corpora.")
    parser.add_argument("--check", action="store_true", help="compare instead of writing")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="corpus directory")
    args = parser.parse_args(argv)
    differ = []
    for lang in LANGUAGES:
        path = args.out / f"synthetic.{lang}.jsonl"
        content = generate(lang)
        if args.check:
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current != content:
                differ.append(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    for path in differ:
        print(f"{path} differs from the generated corpus", file=sys.stderr)
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
