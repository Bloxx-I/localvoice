"""Conservative exact word overlap removal between adjacent ASR chunks."""
import re


def append_text(existing, incoming):
    previous, current = existing.split(), incoming.strip().split()
    normalize = lambda word: re.sub(r'[^\w]', '', word).casefold()
    skip = 0
    for count in range(1, min(16, len(previous), len(current)) + 1):
        if [normalize(w) for w in previous[-count:]] == [normalize(w) for w in current[:count]]:
            skip = count
    addition = ' '.join(current[skip:])
    return (existing.rstrip() + (' ' if existing and addition else '') + addition).strip()
