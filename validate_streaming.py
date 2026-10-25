import json, subprocess, sys, time
from pathlib import Path
root=Path(__file__).resolve().parent
report=json.loads((root/'hardware-validation.json').read_text())
segments=report['recordings'][0]['segments']
target=root/'Pruefaufnahmen/live-validation.txt'
for event in segments: event['target']=str(target)
payload=''.join(json.dumps(e)+'\n' for e in segments)+'{"finish":true}\n'
started=time.monotonic()
proc=subprocess.run([sys.executable,str(root/'src/stream_transcribe.py'),'--model',str(root/'models/qwen3-asr'),'--language','de'],input=payload,text=True,capture_output=True,timeout=180)
events=[]
for line in proc.stdout.splitlines():
 try: events.append(json.loads(line))
 except ValueError: pass
result={'exit_code':proc.returncode,'elapsed_seconds':time.monotonic()-started,
        'completed_chunks':[e['completed'] for e in events if 'completed' in e],
        'inference_seconds':[e['inference_seconds'] for e in events if 'inference_seconds' in e],
        'finished':any(e.get('finished') for e in events),
        'transcript_characters':len(target.read_text()) if target.exists() else None,
        'stderr_tail':proc.stderr[-1000:] if proc.returncode else ''}
(root/'streaming-validation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if proc.returncode: sys.exit(proc.returncode)
