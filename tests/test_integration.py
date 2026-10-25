import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
import numpy as np
import soxr
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from import_usb import import_recording
from stream_text import append_text
from engine import Capture, InputSpec


class IntegrationTests(unittest.TestCase):
    def test_usb_import_preserves_source_and_channel_at_48k(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'usb.wav'
            t=np.arange(16000)/16000
            samples=np.column_stack((.1*np.sin(2*np.pi*440*t), .2*np.sin(2*np.pi*880*t)))
            pcm=(samples*2147483647).astype('<i4')
            with wave.open(str(source),'wb') as w:
                w.setparams((2,4,16000,0,'NONE','not compressed')); w.writeframes(pcm.tobytes())
            before=source.read_bytes()
            result=import_recording(source,Path(folder)/'derived',channel='2')
            self.assertEqual(source.read_bytes(),before)
            self.assertEqual(result['source_sample_rate'],16000)
            import soundfile as sf, lecture
            info=sf.info(result['clean'])
            self.assertEqual((info.samplerate,info.channels,info.format,info.subtype),(48000,1,'FLAC','PCM_24'))
            # The text track reads the untouched USB file, not a converted copy.
            self.assertEqual(lecture.board_source(result['clean']).resolve(),source.resolve())

    def test_identical_channels_are_stored_once(self):
        for second, expect in ((lambda a: a+3, 1), (lambda a: a//2, 2)):
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'native.wav'
                info={'name':'Synthetic','default_samplerate':16000,'max_input_channels':2}
                with patch('engine.sd.query_devices',return_value=info), patch('engine.sd.InputStream'):
                    capture=Capture(InputSpec(0,'Synthetic'),path)
                    left=np.arange(-50000,50000,7,dtype=np.int32)*1000
                    data=np.column_stack((left,second(left))).astype(np.int32)
                    capture.callback(data,len(data),None,None); capture.drain(); capture.close()
                    capture.collapse_identical_channels()
                with wave.open(str(path),'rb') as w:
                    self.assertEqual(w.getnchannels(),expect)
                    if expect==1:
                        self.assertEqual(w.readframes(len(left)),left.astype('<i4').tobytes())

    def test_native_capture_saves_every_channel_before_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'native.wav'
            info={'name':'Synthetic','default_samplerate':16000,'max_input_channels':2}
            with patch('engine.sd.query_devices',return_value=info), patch('engine.sd.InputStream'):
                capture=Capture(InputSpec(0,'Synthetic',channel=1),path)
                data=np.array([[1,123456789],[-1234,-2147483648],[2147483647,0]],dtype=np.int32)
                capture.callback(data,len(data),None,None); capture.drain()
                capture.append(np.empty(0,np.float32),last=True); capture.close()
            with wave.open(str(path),'rb') as w:
                self.assertEqual((w.getnchannels(),w.getframerate(),w.getsampwidth()),(2,16000,4))
                self.assertEqual(w.readframes(3),data.astype('<i4').tobytes())

    def test_live_text_overlap(self):
        self.assertEqual(append_text('Das ist ein Test.','ein Test und noch mehr.'),'Das ist ein Test. und noch mehr.')
        self.assertEqual(append_text('', 'Hallo Welt.'),'Hallo Welt.')
        self.assertEqual(append_text('Hallo Welt.', ''),'Hallo Welt.')

if __name__=='__main__': unittest.main()
