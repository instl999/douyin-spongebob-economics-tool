"""
gen_sfx.py — 生成标准音效库
生成6个音效: pop(标签弹出), whoosh(元素滑入), ding(关键词高亮),
coin(金币/利润), scribble(写字画图), splash(转场)
输出为 44100Hz 16bit stereo WAV。
"""
import struct
import wave
from pathlib import Path

import numpy as np

SR = 44100


def write_wav(path, samples):
    """samples: float32 array, shape (N,) 或 (N,2)"""
    if samples.ndim == 1:
        samples = np.column_stack([samples, samples])
    samples = np.clip(samples, -1.0, 1.0)
    int16 = (samples * 32767).astype(np.int16)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(int16.tobytes())


def env_adsr(n, attack=0.01, decay=0.1, sustain=0.7, release=0.2):
    """简单包络"""
    t = np.linspace(0, 1, n)
    env = np.ones(n)
    a = int(n * attack)
    d = int(n * decay)
    r = int(n * release)
    if a > 0:
        env[:a] = np.linspace(0, 1, a)
    if d > 0:
        env[a:a + d] = np.linspace(1, sustain, d)
    if r > 0:
        env[-r:] = np.linspace(sustain, 0, r)
    return env


def gen_pop(dur=0.12):
    """短促弹出声：正弦波 900→500Hz 快速下扫"""
    n = int(SR * dur)
    t = np.linspace(0, dur, n)
    freq = 900 - 400 * (t / dur) ** 0.5
    phase = 2 * np.pi * np.cumsum(freq) / SR
    sig = 0.6 * np.sin(phase)
    env = np.exp(-t * 25) * (1 - np.exp(-t * 200))
    return sig * env


def gen_whoosh(dur=0.35):
    """滑入声：白噪声+带通，频率从 300→3000Hz 扫过"""
    n = int(SR * dur)
    t = np.linspace(0, dur, n)
    noise = np.random.randn(n) * 0.3
    # 简单带通：用差分近似高通 + 滑动平均低通
    freq_center = 300 + 2700 * (t / dur) ** 1.5
    # 用相位调制的噪声模拟 whoosh
    sig = noise * np.sin(2 * np.pi * np.cumsum(freq_center) / SR)
    env = env_adsr(n, attack=0.05, decay=0.1, sustain=0.6, release=0.3)
    return sig * env * 0.5


def gen_ding(dur=0.5):
    """高亮叮声：1200Hz + 1800Hz 正弦，指数衰减"""
    n = int(SR * dur)
    t = np.linspace(0, dur, n)
    sig = 0.4 * np.sin(2 * np.pi * 1200 * t) + 0.25 * np.sin(2 * np.pi * 1800 * t)
    env = np.exp(-t * 6)
    return sig * env


def gen_coin(dur=0.3):
    """金币声：快速上升的双音 + 衰减"""
    n = int(SR * dur)
    t = np.linspace(0, dur, n)
    freq1 = 988 * (1 + 0.1 * (1 - np.exp(-t * 30)))  # B5
    freq2 = 1319 * (1 + 0.1 * (1 - np.exp(-t * 30)))  # E6
    phase1 = 2 * np.pi * np.cumsum(freq1) / SR
    phase2 = 2 * np.pi * np.cumsum(freq2) / SR
    sig = 0.35 * np.sin(phase1) + 0.25 * np.sin(phase2)
    env = np.exp(-t * 8) * (1 - np.exp(-t * 150))
    return sig * env


def gen_scribble(dur=0.4):
    """写字声：短促噪声脉冲序列"""
    n = int(SR * dur)
    t = np.linspace(0, dur, n)
    # 多个短促脉冲
    sig = np.zeros(n)
    pulse_count = 8
    for i in range(pulse_count):
        start = int(n * i / pulse_count)
        plen = int(n * 0.08)
        end = min(n, start + plen)
        sig[start:end] = np.random.randn(end - start) * 0.4
    env = env_adsr(n, attack=0.02, decay=0.05, sustain=0.8, release=0.1)
    return sig * env


def gen_splash(dur=0.4):
    """转场水花声：白噪声+低通，快升慢降"""
    n = int(SR * dur)
    t = np.linspace(0, dur, n)
    noise = np.random.randn(n)
    # 简单低通：滑动平均
    kernel = np.ones(80) / 80
    filtered = np.convolve(noise, kernel, mode="same")
    env = np.exp(-t * 5) * (1 - np.exp(-t * 80))
    return filtered * env * 0.6


SFX_GENERATORS = {
    "pop": gen_pop,
    "whoosh": gen_whoosh,
    "ding": gen_ding,
    "coin": gen_coin,
    "scribble": gen_scribble,
    "splash": gen_splash,
}


def generate_all(output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, gen in SFX_GENERATORS.items():
        sig = gen()
        write_wav(output_dir / f"{name}.wav", sig)
        print(f"  [ok] {name}.wav  ({len(sig)/SR:.2f}s)")
    print(f"音效库已生成到: {output_dir}")


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "./sfx"
    generate_all(out)
