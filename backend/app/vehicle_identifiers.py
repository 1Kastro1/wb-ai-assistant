"""Vehicle identifiers shared by catalog routing, evidence and privacy handling."""
import re

VIN = re.compile(r'(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Z0-9])', re.I)
FRAME = re.compile(r'(?<![A-Z0-9])([A-Z]{1,4}[0-9]{1,3}[A-Z]?)\s*[-‐‑–—]\s*([0-9]{5,8})(?![A-Z0-9])', re.I)


def vehicle_identifiers(text):
    text = text or ''
    found = {v.upper() for v in VIN.findall(text)}
    for match in FRAME.finditer(text):
        # Explicit OEM/part-number labels must not be interpreted as vehicle identity.
        if re.search(r'(?:OEM|OE|ОЕМ|артикул|номер детали)\s*[:#№]?\s*$', text[max(0, match.start()-24):match.start()], re.I):
            continue
        found.add(match[1].upper()+'-'+match[2])
    return sorted(found)


def valid_vehicle_identifier(value):
    return bool(VIN.fullmatch(value) or FRAME.fullmatch(value))


def identifier_key(value):
    return re.sub(r'\s|[-‐‑–—]', '', value.upper()) if FRAME.fullmatch(value) else value.upper()


def mask_frames(text):
    return FRAME.sub(lambda m: m[1].upper()+'-*****'+m[2][-2:], text)
