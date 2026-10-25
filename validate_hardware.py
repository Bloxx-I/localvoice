import json, sys, time, wave
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from engine import Recorder, InputSpec, input_devices
from coreaudio_rates import hardware_rates
root=Path(__file__).resolve().parent
index,info=next((i,d) for i,d in input_devices() if 'respeaker' in d['name'].lower())
report={'hardware':hardware_rates().get(info['name']), 'recordings':[]}
for pipeline,seconds in [('live',6),('deferred',1)]:
    rec=Recorder([InputSpec(index,info['name'])],root/'Pruefaufnahmen',pipeline=pipeline,segment_seconds=3)
    rec.start()
    deadline=time.monotonic()+20
    while rec.state['status'] not in ('recording','error','done') and time.monotonic()<deadline: time.sleep(.02)
    if rec.state['status']!='recording': raise RuntimeError(rec.error or 'Audio startet nicht')
    end=time.monotonic()+seconds
    while time.monotonic()<end and rec.state['status']=='recording': time.sleep(.05)
    rec.stop(); rec.thread.join(15)
    if rec.thread.is_alive(): raise RuntimeError('Aufnahme beendet nicht')
    if rec.error: raise RuntimeError(rec.error)
    entry={'pipeline':pipeline,'duration':rec.result['duration_seconds'],'native':rec.result['native_inputs'],
           'clean':rec.result['clean'],'original':rec.result['original'],'missing':rec.result['missing_frames'], 'segments':[]}
    while not rec.segments.empty(): entry['segments'].append(rec.segments.get())
    for name in ['clean','original']:
        with wave.open(entry[name],'rb') as f: entry[name+'_format']=[f.getframerate(),f.getnchannels(),f.getsampwidth()*8]
    report['recordings'].append(entry)
(root/'hardware-validation.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
