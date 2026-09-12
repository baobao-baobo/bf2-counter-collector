#!/usr/bin/env python3
# tflite_bench.py - loop TFLite inference (MobileNetV1-quant) for the
# G6 app-phase runs.
#
# Usage: python3 tflite_bench.py MODEL THREADS ITERS
# ITERS is chosen by calibration so the whole run lands inside the
# -t 40 app window (target ~30-35 s, natural exit).
import sys
import time

import numpy as np
from tflite_runtime.interpreter import Interpreter


def main():
    model, threads, iters = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])

    interp = Interpreter(model_path=model, num_threads=threads)
    interp.allocate_tensors()
    inp = interp.get_input_details()[0]

    # random uint8 input with a fixed seed: values do not matter, only
    # the compute/memory path does, and reproducibility across runs
    rng = np.random.default_rng(42)
    data = rng.integers(0, 256, inp["shape"], dtype=np.uint8)
    interp.set_tensor(inp["index"], data)

    for _ in range(3):          # warmup: thread pool and arena alloc
        interp.invoke()

    t0 = time.time()
    for _ in range(iters):
        interp.invoke()
    dt = time.time() - t0

    print("iters=%d threads=%d time=%.3fs per_iter=%.2fms"
          % (iters, threads, dt, dt / iters * 1000.0))


if __name__ == "__main__":
    main()
