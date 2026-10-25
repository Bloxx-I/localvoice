"""Reprocess an existing original into a new pair, preserving all source files."""
import argparse
import json
import shutil
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
from engine import Enhancer, FRAME, RATE, pcm


def enhance(source, destination=None, attenuation=18):
    source = Path(source)
    folder = Path(destination) if destination else source.parent
    folder.mkdir(parents=True,exist_ok=True)
    profile = {18:'Natürlich',30:'Ausgewogen',45:'Mehr_Ruhe'}.get(attenuation,f'{attenuation:g}dB')
    stem = 'LocalVoice_'+datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')+'_'+profile
    clean_path = folder/(stem+'_clean.wav')
    raw_path = folder/(stem+'_original.wav')
    model = Enhancer(1, attenuation=attenuation)
    started = time.perf_counter()
    samples = 0
    try:
        with wave.open(str(source),'rb') as raw, wave.open(str(clean_path),'wb') as clean:
            if raw.getnchannels()!=1 or raw.getsampwidth() not in (2,4) or raw.getframerate()!=RATE:
                raise ValueError('Quelle muss eine LocalVoice-Mono-Aufnahme mit 48 kHz sein.')
            clean.setparams((1,4,RATE,0,'NONE','not compressed'))
            while True:
                data = raw.readframes(FRAME)
                if not data: break
                x = np.frombuffer(data,dtype='<i2' if raw.getsampwidth()==2 else '<i4').astype(np.float32)/(32768 if raw.getsampwidth()==2 else 2147483648)
                n = len(x)
                if n < FRAME: x = np.pad(x,(0,FRAME-n))
                y,_ = model.process([x])
                clean.writeframesraw(pcm(y[:n]))
                samples += n
            tail_frames = model.delay_samples//FRAME+1
            for _ in range(tail_frames):
                zeros = np.zeros(FRAME,np.float32)
                y,_ = model.process([zeros])
                clean.writeframesraw(pcm(y))
                samples += FRAME
        shutil.copy2(source,raw_path)
        metadata = {'source':source.name,'model':'DeepFilterNet3 studio','attenuation_limit_db':attenuation,
            'model_delay_samples':model.delay_samples,'model_tail_ms':tail_frames*10,
            'duration_seconds':samples/RATE,'elapsed_seconds':time.perf_counter()-started,
            'local_only':True,'sample_rate':RATE}
        source_meta = source.with_name(source.name.replace('_original.wav','.json'))
        if source_meta.exists():
            try:
                original_meta = json.loads(source_meta.read_text())
                metadata['native_inputs'] = original_meta.get('native_inputs',[])
                metadata['source_metadata'] = str(source_meta)
            except (OSError,ValueError):
                pass
        (folder/(stem+'.json')).write_text(json.dumps(metadata,indent=2))
        return str(clean_path),str(raw_path),metadata
    finally: model.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('--destination')
    parser.add_argument('--attenuation',type=float,default=18)
    args = parser.parse_args()
    clean,raw,metadata = enhance(args.source,args.destination,args.attenuation)
    print(json.dumps({'clean':clean,'original':raw,**metadata},indent=2))
