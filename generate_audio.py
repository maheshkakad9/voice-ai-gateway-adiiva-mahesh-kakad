import os
import wave
import struct
import math

os.makedirs('app/assets', exist_ok=True)

sample_rate = 16000
duration = 2.0
frequencies = [261.63, 329.63, 392.00, 523.25] # C4, E4, G4, C5 (C Major)

num_samples = int(sample_rate * duration)
audio_data = []

for i in range(num_samples):
    t = i / sample_rate
    sample = 0
    # Fade in each note slightly offset to create a pleasant arpeggio
    for j, f in enumerate(frequencies):
        start_t = j * 0.1
        if t > start_t:
            env = math.exp(-1.5 * (t - start_t))
            sample += env * math.sin(2 * math.pi * f * t)
    
    # Normalize and scale to 16-bit
    sample = sample / len(frequencies)
    val = int(32767 * sample * 0.8) # 80% volume
    val = max(-32768, min(32767, val))
    audio_data.append(struct.pack("<h", val))

with wave.open('app/assets/demo.wav', 'wb') as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(sample_rate)
    wf.writeframes(b''.join(audio_data))

print("Created app/assets/demo.wav successfully!")
