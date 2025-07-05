import sys, os, subprocess, time
from datetime import datetime

import cv2, numpy as np
import sounddevice as sd, soundfile as sf
from PySide6.QtWidgets import (
    QApplication, QWidget, QPushButton, QLabel, QVBoxLayout, QSlider, QSpinBox
)
from PySide6.QtCore import Qt, QThread, Signal

# ────────────────────────── Audio Spike Monitor ────────────────────────── #
class AudioMonitor(QThread):
    spike_detected = Signal()
    def __init__(self, threshold_db: int):
        super().__init__(); self.threshold_db = threshold_db; self.running = False
    def run(self):
        self.running = True
        def cb(indata, *_):
            if not self.running: return
            db = 20 * np.log10(np.sqrt(np.mean(indata**2)) + 1e-6)
            if db > self.threshold_db: self.spike_detected.emit()
        with sd.InputStream(callback=cb):
            while self.running: sd.sleep(100)
    def stop(self): self.running = False

# ───────────────────────── Parallel AV Recorder ───────────────────────── #
class RecorderThread(QThread):
    finished_recording = Signal(str)
    def __init__(self, duration: int): super().__init__(); self.duration = duration
    def run(self):
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        v_tmp, a_tmp, final = f'v_{ts}.mp4', f'a_{ts}.wav', f'sleep_{ts}.mp4'
        fps_target, sr, ch = 20, 44100, 1
        w, h = 640, 480

        cap = cv2.VideoCapture(0); cap.set(3, w); cap.set(4, h)
        vw = cv2.VideoWriter(v_tmp, cv2.VideoWriter_fourcc(*'mp4v'), fps_target, (w, h))

        audio_frames = []
        def audio_cb(indata, *_): audio_frames.append(indata.copy())
        stream = sd.InputStream(callback=audio_cb, samplerate=sr, channels=ch); stream.start()

        frame_interval = 1.0 / fps_target
        next_frame_t = time.perf_counter()
        frames_written = 0
        end_t = next_frame_t + self.duration
        while time.perf_counter() < end_t:
            ret, frame = cap.read()
            if ret:
                vw.write(frame); frames_written += 1
            next_frame_t += frame_interval
            sleep_for = next_frame_t - time.perf_counter()
            if sleep_for > 0: time.sleep(sleep_for)
        elapsed = self.duration  # by construction

        # Cleanup capture
        stream.stop(); cap.release(); vw.release()
        sf.write(a_tmp, np.concatenate(audio_frames), sr)

        # Adjust FPS if frames dropped/enqueued extra
        actual_fps = frames_written / elapsed if elapsed else fps_target
        if abs(actual_fps - fps_target) > 0.1:
            fixed_video = f'vfix_{ts}.mp4'
            subprocess.run(['ffmpeg','-y','-i',v_tmp,'-r',f'{actual_fps:.4f}',fixed_video],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.remove(v_tmp); v_tmp = fixed_video

        # Mux A+V
        subprocess.run([
            'ffmpeg','-y','-i',v_tmp,'-i',a_tmp,
            '-c:v','copy','-c:a','aac','-movflags','+faststart','-shortest',final
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.remove(v_tmp); os.remove(a_tmp)
        self.finished_recording.emit(final)

# ────────────────────────────── GUI App ───────────────────────────────── #
class SleepMonitorApp(QWidget):
    def __init__(self):
        super().__init__(); self.setWindowTitle('Sleep Monitor — Synced MP4 Recorder')
        self.status = QLabel('Status: Idle')
        self.start_btn, self.stop_btn = QPushButton('Start Monitoring'), QPushButton('Stop Monitoring')
        self.slider, self.th_label = QSlider(Qt.Horizontal), QLabel('Threshold (dB): -30')
        self.slider.setRange(-60,0); self.slider.setValue(-30)
        self.dur_box = QSpinBox(); self.dur_box.setRange(5,600); self.dur_box.setValue(10); self.dur_box.setSuffix(' sec')
        lay = QVBoxLayout(self); [lay.addWidget(w) for w in (self.status,self.th_label,self.slider,self.dur_box,self.start_btn,self.stop_btn)]
        self.slider.valueChanged.connect(lambda v: self.th_label.setText(f'Threshold (dB): {v}'))
        self.start_btn.clicked.connect(self.start_mon); self.stop_btn.clicked.connect(self.stop_mon)
        self.mon: AudioMonitor|None = None; self.rec: RecorderThread|None = None; self.rec_active = False
    def start_mon(self):
        self.mon = AudioMonitor(self.slider.value()); self.mon.spike_detected.connect(self.on_spike); self.mon.start()
        self.status.setText('Status: Listening…')
    def stop_mon(self):
        if self.mon: self.mon.stop(); self.mon.wait(); self.mon=None; self.status.setText('Status: Stopped')
    def on_spike(self):
        if self.rec_active: return
        self.rec_active=True; self.status.setText('Status: Recording…')
        self.rec = RecorderThread(self.dur_box.value()); self.rec.finished_recording.connect(self.on_done); self.rec.start()
    def on_done(self,f): self.status.setText(f'Saved: {f} — Listening…'); self.rec_active=False
    def closeEvent(self,e): self.stop_mon(); super().closeEvent(e)

# ───────────────────────────── Entrypoint ─────────────────────────────── #
if __name__ == '__main__':
    QApplication.setApplicationName('SleepMonitor')
    app = QApplication(sys.argv); w = SleepMonitorApp(); w.show(); sys.exit(app.exec())