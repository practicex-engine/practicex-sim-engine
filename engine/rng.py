"""재현 가능한 난수. 같은 (master_seed, 가상일 인덱스)는 항상 같은 난수열을 준다."""
import hashlib
import random


def day_rng(master_seed, index, stream="main"):
    h = hashlib.sha256(f"{master_seed}:{index}:{stream}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(h[:8], "big"))
