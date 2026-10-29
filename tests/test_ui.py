import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PySide6.QtWidgets import QApplication
from combined import Combined


class UIRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application=QApplication.instance() or QApplication([])

    def test_live_completion_releases_studio_actions(self):
        w=Combined()
        w.timer.stop();w.studio.timer.stop();w.usb.timer.stop()
        w.studio.save_preferences=lambda: None
        w.studio.last={'clean':'example_clean.wav','original':'example_original.wav'}
        w.studio.enhance_button.setEnabled(False);w.studio.open_pair.setEnabled(False)
        w.studio.live_stt=object()
        w.studio.release_pipeline_controls()
        self.assertFalse(w.studio.enhance_button.isEnabled())
        w.studio.live_stt=None
        w.studio.release_pipeline_controls()
        self.assertTrue(w.studio.enhance_button.isEnabled())
        self.assertTrue(w.studio.open_pair.isEnabled())
        self.assertTrue(w.studio.pipeline_mode.isEnabled())
        w.close()

    def test_simple_mode_shows_hear_button(self):
        w = Combined()
        w.timer.stop(); w.studio.timer.stop(); w.usb.timer.stop()
        w.show()
        w.studio.set_expert(False)
        self.assertFalse(w.studio.simple_bar.isHidden())
        self.assertTrue(w.studio.compare_panel.isHidden())
        self.assertEqual(w.studio.listen_button.text(), '▶  Anhören')
        w.studio.set_expert(True)
        self.assertTrue(w.studio.simple_bar.isHidden())
        self.assertFalse(w.studio.compare_panel.isHidden())
        self.assertEqual(w.studio.listen_button.text(), '▶  Nachhören')
        w.close()

if __name__=='__main__': unittest.main()
