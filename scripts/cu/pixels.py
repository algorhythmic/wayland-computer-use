"""Bounded RGB buffers. Encode only at the MCP delivery boundary."""
import os
import struct
import threading
import zlib

MAX_PIXELS = 16_000_000
PARALLEL_MIN_BYTES = 2_000_000  # Below this, thread start-up outweighs the deflate split.
PARALLEL_STREAMS = max(1, min(4, os.cpu_count() or 1))


def deflate(data, level=1, streams=PARALLEL_STREAMS):
    """One valid zlib stream built from independent raw-deflate members (as pigz does).

    Each member compresses its own slice and ends on a byte boundary with a sync
    flush; the last member finishes the stream. zlib releases the GIL, so members
    compress concurrently. Output is lossless and decodes with any inflater.
    """
    if streams <= 1 or len(data) < PARALLEL_MIN_BYTES:
        return zlib.compress(data, level)
    bounds = [(i*len(data)//streams, (i+1)*len(data)//streams) for i in range(streams)]
    members = [None]*streams
    def compress(index):
        start, end = bounds[index]
        compressor = zlib.compressobj(level, zlib.DEFLATED, -15)
        members[index] = compressor.compress(data[start:end]) + \
            compressor.flush(zlib.Z_FINISH if index == streams-1 else zlib.Z_SYNC_FLUSH)
    threads = [threading.Thread(target=compress, args=(i,)) for i in range(1, streams)]
    for thread in threads:
        thread.start()
    compress(0)
    for thread in threads:
        thread.join()
    header = b'\x78\x01' if level < 2 else b'\x78\x9c'
    return header + b''.join(members) + struct.pack('!I', zlib.adler32(data) & 0xffffffff)


def parse_ppm(data):
    # P6 allows comments between header tokens. Consume exactly one delimiter
    # after maxval: whitespace and '#' may be the first byte of a real pixel.
    pos = 0
    def token():
        nonlocal pos
        while pos < len(data):
            if data[pos] in b' \t\r\n':
                pos += 1
            elif data[pos] == 35:
                end = data.find(b'\n', pos)
                if end < 0:
                    raise ValueError('Truncated PPM comment')
                pos = end + 1
            else:
                break
        start = pos
        while pos < len(data) and data[pos] not in b' \t\r\n#':
            pos += 1
        if pos == start or pos > 4096:
            raise ValueError('Invalid PPM header')
        return data[start:pos]
    if token() != b'P6':
        raise ValueError('Expected binary RGB PPM')
    width, height, maximum = int(token()), int(token()), int(token())
    if width < 1 or height < 1 or width*height > MAX_PIXELS or maximum != 255:
        raise ValueError('Unsupported PPM dimensions or depth')
    if pos >= len(data) or data[pos] not in b' \t\r\n':
        raise ValueError('Missing PPM separator')
    pos += 1
    rgb = data[pos:]
    if len(rgb) != width*height*3:
        raise ValueError('Invalid PPM payload size')
    return width, height, rgb


def png_rgb(width, height, rgb):
    if not 0 < width*height <= MAX_PIXELS or len(rgb) != width*height*3:
        raise ValueError('Invalid RGB size')
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind+data))
    rows = b''.join(b'\0'+rgb[y*width*3:(y+1)*width*3] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', deflate(rows, 1)) + chunk(b'IEND', b''))


def crop(rgb, width, box):
    x, y, right, bottom = box
    height = len(rgb)//(width*3)
    if not (0 <= x < right <= width and 0 <= y < bottom <= height):
        raise ValueError('Crop outside capture')
    return b''.join(rgb[(row*width+x)*3:(row*width+right)*3] for row in range(y, bottom))


def changed_box(before, after, width, height, tile=64):
    if len(before) != len(after) or len(after) != width*height*3:
        raise ValueError('Incompatible pixel buffers')
    if before == after:
        return None
    changed = []
    for y in range(0, height, tile):
        for x in range(0, width, tile):
            right, bottom = min(x+tile, width), min(y+tile, height)
            if any(before[(row*width+x)*3:(row*width+right)*3] !=
                   after[(row*width+x)*3:(row*width+right)*3] for row in range(y, bottom)):
                changed.append((x, y, right, bottom))
    return [min(r[0] for r in changed), min(r[1] for r in changed),
            max(r[2] for r in changed), max(r[3] for r in changed)]
