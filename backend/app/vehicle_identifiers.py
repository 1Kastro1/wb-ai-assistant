"""Vehicle identifiers shared by catalog routing, evidence and privacy handling."""
import re

VIN = re.compile(r'(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Z0-9])', re.I)
FRAME = re.compile(r'(?<![A-Z0-9])([A-Z]{1,4}[0-9]{1,3}[A-Z]?)\s*[-‐‑–—]\s*([0-9]{5,8})(?![A-Z0-9])', re.I)
COMPACT_FRAME = re.compile(r'(?<![A-Z0-9])([A-Z]{2,4}[0-9]{2}[A-Z]?)([0-9]{6,7})(?![A-Z0-9])', re.I)
PART_LABEL = re.compile(r'(?:OEM|OE|ОЕМ|артикул|номер детали)\s*[:#№]?\s*$', re.I)
VEHICLE_CONTEXT = re.compile(r'\b(?:frame|фрейм\w*|кузов\w*|под[оа]йд\w*|совместим\w*)\b', re.I)


def frame_matches(text):
    for pattern in (FRAME, COMPACT_FRAME):
        for match in pattern.finditer(text):
            if PART_LABEL.search(text[max(0, match.start()-24):match.start()]):
                continue
            if pattern is COMPACT_FRAME and not VEHICLE_CONTEXT.search(
                    text[max(0, match.start()-100):match.end()+80]):
                continue
            yield match


def vehicle_identifiers(text):
    text = text or ''
    found = {v.upper() for v in VIN.findall(text)}
    for match in frame_matches(text):
        found.add(match[1].upper()+'-'+match[2])
    return sorted(found)


def valid_vehicle_identifier(value):
    return bool(VIN.fullmatch(value) or FRAME.fullmatch(value) or COMPACT_FRAME.fullmatch(value))


def identifier_key(value):
    return re.sub(r'\s|[-‐‑–—]', '', value.upper()) if FRAME.fullmatch(value) else value.upper()


def mask_frames(text):
    matches = [*FRAME.finditer(text), *(m for m in frame_matches(text) if m.re is COMPACT_FRAME)]
    for match in sorted(matches, key=lambda item: item.start(), reverse=True):
        text = text[:match.start()] + match[1].upper() + '-*****' + match[2][-2:] + text[match.end():]
    return text
