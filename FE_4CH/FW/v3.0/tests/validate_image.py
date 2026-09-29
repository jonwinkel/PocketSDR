"""Independently verify FX3 IMG data against its ELF load segments."""

import argparse
import hashlib
import json
from pathlib import Path
import struct


# Match the IMG loader records against ELF segments without using elf2img.
def validate(image, executable):
    data, elf = image.read_bytes(), executable.read_bytes()
    assert elf[:7] == b'\x7fELF\x01\x01\x01'
    assert data[:4] == b'CY\x1c\xb0'
    entry, phoff = struct.unpack_from('<II', elf, 24)
    phsize, phcount = struct.unpack_from('<HH', elf, 42)
    expected = {}
    for i in range(phcount):
        kind, offset, _, address, filesz, memsz, _, _ = struct.unpack_from('<8I', elf, phoff + i * phsize)
        if kind != 1:
            continue
        segment = elf[offset:offset + filesz] + bytes(memsz - filesz)
        segment += bytes(-len(segment) % 4)
        expected.update((address + j, byte) for j, byte in enumerate(segment) if address + j >= 256)
    actual, blocks, checksum, pos = {}, [], 0, 4
    while True:
        words, address = struct.unpack_from('<II', data, pos)
        pos += 8
        if words == 0:
            assert address == entry
            assert struct.unpack_from('<I', data, pos)[0] == checksum
            assert pos + 4 == len(data)
            break
        block = data[pos:pos + words * 4]
        assert len(block) == words * 4
        checksum = (checksum + sum(struct.unpack('<' + str(words) + 'I', block))) & 0xffffffff
        for j, byte in enumerate(block):
            assert address + j not in actual
            actual[address + j] = byte
        blocks.append({'address': hex(address), 'bytes': len(block)})
        pos += len(block)
    assert actual == expected, 'IMG load data/zero-fill/coverage differs from ELF'
    return {'image': str(image), 'bytes': len(data), 'entry': hex(entry),
            'checksum': hex(checksum), 'load_blocks': blocks,
            'sha256': hashlib.sha256(data).hexdigest(), 'elf_load_data_matches': True}


# Validate a selected image and its unstripped ELF without touching hardware.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("elf", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.image, args.elf), indent=2))


if __name__ == "__main__":
    main()
