#!/usr/bin/env python3
"""Build the bundled offline GeoIP/ASN table consumed by ProxyGeoIp.java.

Data source: the free iptoasn.com dump (https://iptoasn.com/), which maps IPv4
ranges to an AS number, an ISO country code and an AS description. The output is a
compact, memory-mappable binary the app reads with a binary search.

Usage:
    # download the dataset and write the asset in one go:
    python build_geoip_asn.py

    # use a local copy and restrict to a few countries (smaller asset):
    python build_geoip_asn.py --input ip2asn-v4.tsv.gz --countries RU,BY,KZ,UA

Output (default): ../TMessagesProj/src/main/assets/geoip_asn.dat

Binary format (big-endian) — must stay in sync with ProxyGeoIp.java:
    header (16 bytes): magic "ZGIP", version u8=1, 3x reserved, recordCount u32,
                       stringTableOffset u32 (absolute)
    records[recordCount], 14 bytes, sorted ascending by ipStart:
        ipStart u32, ipEnd u32, country 2 bytes (0x0000 = unknown),
        ownerOffset u32 (relative to stringTableOffset, 0xFFFFFFFF = none)
    string table: entries of [u16 length][utf-8 bytes]
"""

import argparse
import gzip
import io
import os
import socket
import struct
import sys
import urllib.request

DATASET_URL = "https://iptoasn.com/data/ip2asn-v4.tsv.gz"

HEADER_SIZE = 16
RECORD_SIZE = 14
NO_OWNER = 0xFFFFFFFF
MAX_OWNER_BYTES = 60  # keep names short enough for a single status line


def ip_to_int(text):
    return struct.unpack(">I", socket.inet_aton(text.strip()))[0]


def clean_owner(description, country):
    if not description:
        return ""
    owner = description.strip()
    # iptoasn often appends ", <CC>" (with or without a space) — drop it; the flag shows the country.
    idx = owner.rfind(",")
    if idx != -1 and country and owner[idx + 1:].strip().upper() == country.upper():
        owner = owner[:idx].strip()
    if owner.lower() in ("not routed", "none", "-", ""):
        return ""
    encoded = owner.encode("utf-8")
    if len(encoded) > MAX_OWNER_BYTES:
        owner = encoded[:MAX_OWNER_BYTES].decode("utf-8", "ignore").strip()
    return owner


def valid_country(code):
    code = (code or "").strip().upper()
    if len(code) == 2 and code.isalpha() and code != "NONE":
        return code
    return ""


def load_rows(stream, countries):
    rows = []
    for raw in stream:
        line = raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray)) else raw
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 5:
            continue
        start_s, end_s, asn_s, country_s, desc = parts[0], parts[1], parts[2], parts[3], parts[4]
        country = valid_country(country_s)
        if countries and country not in countries:
            continue
        try:
            asn = int(asn_s)
        except ValueError:
            asn = 0
        owner = "" if asn == 0 else clean_owner(desc, country)
        if not country and not owner:
            continue
        try:
            ip_start = ip_to_int(start_s)
            ip_end = ip_to_int(end_s)
        except OSError:
            continue
        if ip_end < ip_start:
            continue
        rows.append((ip_start, ip_end, country, owner))
    rows.sort(key=lambda r: r[0])
    return rows


def open_input(input_path):
    if input_path:
        if input_path.endswith(".gz"):
            return gzip.open(input_path, "rb")
        return open(input_path, "rb")
    print("Downloading %s ..." % DATASET_URL)
    with urllib.request.urlopen(DATASET_URL) as resp:
        data = resp.read()
    print("Downloaded %.1f MB" % (len(data) / 1048576.0))
    return gzip.open(io.BytesIO(data), "rb")


def build(rows, out_path):
    # Deduplicate owner strings into a string table.
    owner_offsets = {}
    string_table = bytearray()

    def owner_offset(owner):
        if not owner:
            return NO_OWNER
        cached = owner_offsets.get(owner)
        if cached is not None:
            return cached
        offset = len(string_table)
        encoded = owner.encode("utf-8")
        string_table.extend(struct.pack(">H", len(encoded)))
        string_table.extend(encoded)
        owner_offsets[owner] = offset
        return offset

    record_count = len(rows)
    string_table_offset = HEADER_SIZE + record_count * RECORD_SIZE

    records = bytearray()
    for ip_start, ip_end, country, owner in rows:
        cc = country.encode("ascii") if country else b"\x00\x00"
        if len(cc) != 2:
            cc = b"\x00\x00"
        records.extend(struct.pack(">II", ip_start, ip_end))
        records.extend(cc)
        records.extend(struct.pack(">I", owner_offset(owner)))

    header = bytearray()
    header.extend(b"ZGIP")
    header.append(1)            # version
    header.extend(b"\x00\x00\x00")  # reserved
    header.extend(struct.pack(">I", record_count))
    header.extend(struct.pack(">I", string_table_offset))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(header)
        f.write(records)
        f.write(string_table)

    total = len(header) + len(records) + len(string_table)
    print("Records:       %d" % record_count)
    print("Unique owners: %d" % len(owner_offsets))
    print("Asset size:    %.1f MB" % (total / 1048576.0))
    print("Written to:    %s" % out_path)
    if record_count == 0:
        print("WARNING: no records written — check the input/filters.", file=sys.stderr)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.normpath(os.path.join(here, "..", "TMessagesProj", "src", "main", "assets", "geoip_asn.dat"))

    parser = argparse.ArgumentParser(description="Build geoip_asn.dat for ProxyGeoIp.")
    parser.add_argument("--input", help="Local ip2asn-v4 .tsv or .tsv.gz (otherwise downloaded).")
    parser.add_argument("--output", default=default_out, help="Output .dat path.")
    parser.add_argument("--countries", help="Comma-separated ISO codes to keep (e.g. RU,BY,KZ). Omit for worldwide.")
    args = parser.parse_args()

    countries = None
    if args.countries:
        countries = {c.strip().upper() for c in args.countries.split(",") if c.strip()}
        print("Filtering to countries: %s" % ", ".join(sorted(countries)))

    with open_input(args.input) as stream:
        rows = load_rows(stream, countries)
    build(rows, args.output)


if __name__ == "__main__":
    main()
