"""
Benchmark Qwen2.5-VL-7B-AWQ en la GPU local.

Mide:
    • latencia por inferencia (mediana + p95)
    • pico de VRAM
    • tokens/s de salida

Uso:
    cd "SERVER-IA PERIMETRALES"
    python tools/bench_qwen_vlm.py                       # modelo y crop por defecto
    python tools/bench_qwen_vlm.py --image path.jpg      # con un crop real
    python tools/bench_qwen_vlm.py --runs 20 --warmup 3
    python tools/bench_qwen_vlm.py --model Qwen/Qwen2-VL-2B-Instruct   # fallback más liviano

Requisitos:
    pip install transformers>=4.49 accelerate autoawq qwen-vl-utils pillow torch
"""
from __future__ import annotations

import argparse
import statistics
import time
from pathlib import Path

import torch
from PIL import Image
from transformers import AutoModelForVision2Seq, AutoProcessor

PROMPT = (
    "Observa la imagen: ¿una persona está siendo atendida en la caja "
    "registrando un pedido de comida? Responde solo SI o NO."
)


def _build_dummy_crop(size: int = 448) -> Image.Image:
    """Crop sintético con gradiente + rectángulo (sustituto de persona+caja)."""
    img = Image.new("RGB", (size, size), (210, 200, 190))
    pixels = img.load()
    for y in range(size):
        for x in range(size):
            pixels[x, y] = (
                (200 + x // 8) % 256,
                (180 + y // 8) % 256,
                160,
            )
    # Rectángulo oscuro central simulando un torso
    for y in range(size // 4, 3 * size // 4):
        for x in range(3 * size // 8, 5 * size // 8):
            pixels[x, y] = (60, 60, 80)
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen2.5-VL-7B-Instruct-AWQ")
    ap.add_argument("--image", default=None, help="ruta a un crop real (opcional)")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--max-new-tokens", type=int, default=8)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args()

    print(f"[bench] modelo={args.model} device={args.device}")
    print(f"[bench] runs={args.runs} warmup={args.warmup} max_new_tokens={args.max_new_tokens}")
    if args.device == "cuda":
        cap = torch.cuda.get_device_capability(0)
        name = torch.cuda.get_device_name(0)
        print(f"[bench] GPU={name} compute_capability={cap[0]}.{cap[1]}")
        if cap[0] < 8:
            print("[bench] AVISO: cc<8 → kernels AWQ optimizados no disponibles, "
                  "transformers usará fallback más lento.")

    # Carga del modelo (AWQ se autodetecta vía config.quantization_config)
    t0 = time.time()
    processor = AutoProcessor.from_pretrained(args.model, trust_remote_code=True)
    model = AutoModelForVision2Seq.from_pretrained(
        args.model,
        torch_dtype=torch.float16,
        device_map=args.device,
        trust_remote_code=True,
    )
    model.eval()
    load_s = time.time() - t0
    print(f"[bench] modelo cargado en {load_s:.1f} s")

    if args.device == "cuda":
        torch.cuda.reset_peak_memory_stats()
        mem_after_load = torch.cuda.memory_allocated() / 1e9
        print(f"[bench] VRAM tras carga: {mem_after_load:.2f} GB")

    # Imagen
    if args.image and Path(args.image).is_file():
        img = Image.open(args.image).convert("RGB")
        # Recorta/redimensiona a 448 para simular un crop estandarizado
        img.thumbnail((448, 448))
        print(f"[bench] usando imagen real: {args.image} ({img.size})")
    else:
        img = _build_dummy_crop(448)
        print("[bench] usando crop sintético 448x448")

    # Mensaje en formato Qwen2.5-VL
    messages = [
        {"role": "user", "content": [
            {"type": "image", "image": img},
            {"type": "text",  "text": PROMPT},
        ]},
    ]
    text = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True,
    )
    inputs = processor(
        text=[text], images=[img], padding=True, return_tensors="pt",
    ).to(args.device)

    def _one_call() -> tuple[float, str]:
        t = time.time()
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=args.max_new_tokens,
                do_sample=False,
            )
        if args.device == "cuda":
            torch.cuda.synchronize()
        dt = time.time() - t
        gen = out[0, inputs["input_ids"].shape[1]:]
        answer = processor.decode(gen, skip_special_tokens=True).strip()
        return dt, answer

    print("\n[bench] warmup...")
    for i in range(args.warmup):
        dt, ans = _one_call()
        print(f"  warmup {i+1}: {dt*1000:7.1f} ms  → {ans!r}")

    print("\n[bench] mediciones...")
    times: list[float] = []
    for i in range(args.runs):
        dt, ans = _one_call()
        times.append(dt)
        print(f"  run {i+1:2d}: {dt*1000:7.1f} ms  → {ans!r}")

    times_ms = [t * 1000 for t in times]
    times_ms.sort()
    p50 = statistics.median(times_ms)
    p95 = times_ms[int(0.95 * (len(times_ms) - 1))]
    avg = statistics.mean(times_ms)
    tps = args.max_new_tokens / statistics.mean(times)

    print("\n[bench] ─── resultados ───")
    print(f"  latencia avg:    {avg:7.1f} ms")
    print(f"  latencia mediana: {p50:7.1f} ms")
    print(f"  latencia p95:    {p95:7.1f} ms")
    print(f"  throughput:      {tps:7.1f} tokens/s salida")
    if args.device == "cuda":
        peak = torch.cuda.max_memory_allocated() / 1e9
        print(f"  VRAM pico:       {peak:7.2f} GB")

    # Veredicto rápido para Hummus (dwell mínimo 1.5 s)
    print("\n[bench] ─── veredicto ───")
    if p95 < 800:
        print("  ✅ p95 < 800ms — verificador VLM viable inline o async sin colas.")
    elif p95 < 2000:
        print("  🟡 p95 entre 0.8-2 s — usar cola asíncrona con drop-head; OK para 1-2 cámaras.")
    else:
        print("  ❌ p95 > 2 s — la Quadro RTX 4000 no rinde con este modelo. "
              "Probá Qwen2-VL-2B-Instruct o un clasificador CLIP+MLP.")


if __name__ == "__main__":
    main()
