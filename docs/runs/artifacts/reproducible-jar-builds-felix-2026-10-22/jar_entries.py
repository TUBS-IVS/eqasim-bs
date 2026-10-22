"""Issue #474: list a jar's entries as "<name> <crc32> <size> <dos time>" lines, sorted by name.

Usage: python jar_entries.py <jar> > listing.txt
Two listings of the same jar built on two machines can then be compared with diff, to see which
entries differ in content (CRC, size) and which only in their timestamps.
"""
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as jar:
    for info in sorted(jar.infolist(), key=lambda entry: entry.filename):
        print("%s %08x %d %04d-%02d-%02dT%02d:%02d:%02d" % ((info.filename, info.CRC, info.file_size) + info.date_time))
