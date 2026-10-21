"""Marketplace replies and imported instructions are plain text, never HTML."""
import re
from html import unescape
from html.parser import HTMLParser


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('br', 'p', 'div', 'li', 'h1', 'h2', 'h3', 'tr') and not self.hidden:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        elif tag in ('p', 'div', 'li', 'tr') and not self.hidden:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value):
    value = str(value or '')
    # Supplier descriptions sometimes contain HTML encoded more than once.
    for _ in range(3):
        decoded = unescape(value)
        if decoded == value:
            break
        value = decoded
    parser = _Text()
    parser.feed(value)
    parser.close()
    value = ''.join(parser.parts)
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b\ufeff]', '', value)
    return '\n'.join(line.strip() for line in re.sub(r'[^\S\n]+', ' ', value).splitlines() if line.strip())
