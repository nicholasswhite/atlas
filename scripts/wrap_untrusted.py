"""Represent external text as data using Python's XML serializer.

The envelope provides provenance and a parseable boundary, not protection against
a model following malicious text. Callers must preserve permission and review
checks independently. This public implementation uses the standard library and
does not include the earlier prototype's third-party boundary-escaping port.
"""
from __future__ import annotations

import argparse
import sys
from xml.etree import ElementTree


def wrap_untrusted(source: str, body: str | None) -> str:
    for value in (source or "unknown", body or ""):
        if any(not (ord(char) in (9, 10, 13) or 0x20 <= ord(char) <= 0xD7FF
                    or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF)
               for char in value):
            raise ValueError("External text contains characters XML 1.0 cannot represent.")
    element = ElementTree.Element("external_content", {"source": source or "unknown"})
    element.text = body or ""
    return ElementTree.tostring(element, encoding="unicode", short_empty_elements=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--body")
    args = parser.parse_args()
    print(wrap_untrusted(args.source, args.body if args.body is not None else sys.stdin.read()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
