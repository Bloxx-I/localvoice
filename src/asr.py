"""Qwen3-ASR on both platforms: MLX on Apple Silicon, PyTorch (qwen-asr) elsewhere."""
import importlib.util
import json
import platform
import sys
from pathlib import Path

import numpy as np

LANGUAGES = {'de': 'German', 'en': 'English', 'fr': 'French', 'es': 'Spanish'}


def backend():
    if sys.platform == 'darwin' and platform.machine() == 'arm64' and importlib.util.find_spec('mlx_audio'):
        return 'mlx'
    return 'torch'


def default_repo():
    """Model to download for this machine (used by download_model.py)."""
    if backend() == 'mlx':
        return 'mlx-community/Qwen3-ASR-1.7B-8bit'
    try:
        import torch
        if torch.cuda.is_available():
            return 'Qwen/Qwen3-ASR-1.7B'
    except ImportError:
        pass
    # Without an NVIDIA GPU the small model keeps a 90-minute lecture practical.
    return 'Qwen/Qwen3-ASR-0.6B'


class ASR:
    def __init__(self, model_dir):
        self.model_dir = Path(model_dir).resolve()
        if not (self.model_dir/'.ready.json').exists():
            raise RuntimeError('Das lokale Sprachmodell fehlt noch.')
        self.kind = backend()
        if self.kind == 'mlx':
            import mlx.core as mx
            from mlx_audio.stt.utils import load_model
            mx.set_cache_limit(256*1024*1024)
            self._mx = mx
            self.model = load_model(self.model_dir, strict=True)
            self.device = 'Apple Silicon (MLX)'
        else:
            import torch
            from qwen_asr import Qwen3ASRModel
            cuda = torch.cuda.is_available()
            self.model = Qwen3ASRModel.from_pretrained(str(self.model_dir), max_new_tokens=512,
                dtype=torch.bfloat16 if cuda else torch.float32, device_map='cuda:0' if cuda else 'cpu')
            self.device = 'NVIDIA-GPU' if cuda else 'Prozessor'

    def describe(self):
        try:
            repo = json.loads((self.model_dir/'.ready.json').read_text()).get('repo', 'Qwen3-ASR')
        except (OSError, ValueError):
            repo = 'Qwen3-ASR'
        return f'{repo.split("/")[-1]} · {self.device}'

    def transcribe(self, audio, language='de', context=''):
        """audio: mono float32 at 16 kHz. Returns plain text."""
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        if not len(audio) or float(np.sqrt(np.mean(audio.astype(np.float64)**2))) < 1e-4:
            return ''
        name = LANGUAGES.get(language)
        if self.kind == 'mlx':
            result = self.model.generate(audio, language=name, max_tokens=512, temperature=0.0,
                                         system_prompt=context or None)
            self._mx.clear_cache()
            return result.text.strip()
        result = self.model.transcribe(audio=(audio, 16000), context=context or '', language=name)
        return result[0].text.strip()
