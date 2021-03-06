from random import random
import pyaudio as pa
from constants import *
import numpy as np
from time import sleep
from tkinter import filedialog
from loop import *
import wave
import math

class AudioPlayer:

    def __init__(self, music_maker):
        self.music_maker = music_maker
        self.metronome = self.music_maker.metronome
        self.percent_through_period = 0
        self.callback_flag = pa.paContinue
        self.active_loops = [-1]
        self.loops = []
        self.loop_buffer_index = 0
        self.previous_volume = 0
        self.base_volume = VOLUME
        self.volume = 0
        self.freq = 440
        self.playing = False
        self.loop_playing = True
        self.loop_recording = False
        self.stream = self.get_stream()
        self.justify_pitch = False

    def get_stream(self):
        global stream
        p = pa.PyAudio()
        stream = p.open(format=pa.paFloat32,
                    channels=1,
                    rate=44100,
                    frames_per_buffer = BUFFER_SIZE,
                    stream_callback = self.callback,
                    output=True)
        stream.pos=0
        return stream

    def stop_stream(self):
        self.callback_flag = pa.paComplete

    def run(self):
        while self.stream.is_active():
            ## Saving and loading the loops must go here because opening a filedialog apparently must happen in the main thread for tkinter
            if keys and is_key_mod(K_S, CTRL):
                filename = filedialog.asksaveasfilename(filetypes=(("Loopsichord Files", ".loops"),("Audio Files", ".wav"), ("All Files", "*.*")))
                if filename:
                    if filename.endswith('.loops'):
                        Loop.save_loops(self.loops, filename=filename)
                    elif filename.endswith('.wav'):
                        self.write_loops(filename)
            if keys and is_key_mod(K_O, CTRL):
                filename = filedialog.askopenfilename(filetypes=(("Loopsichord Files", ".loops"), ("All Files", "*.*")))
                if filename:
                    if filename.endswith('.loops'):
                        self.active_loops = [-1]
                        self.loop_buffer_index = 0
                        loop_list = Loop.load_loops(filename)
                        self.loops = loop_list
                        self.metronome.force_buffer_length(len(self.loops[0].buffers))

            sleep(0.1)
        self.stream.close()

    def callback(self, in_data, frame_count, time_info, flag):
        try:
            if flag:
                print("Playback error: %i" % flag)

            ## Do step is where all the action happens
            self.music_maker.do_step()

            tonic = self.music_maker.scale[0] if self.justify_pitch else None
            self.freq = musical_pitch_to_hertz(self.music_maker.pitch, justify_by_scale=tonic)

            if self.volume != 0:
                ## Generate a sin wave with overtones, starting at the percent through a period where the previous one left off. Return the samples and the percent through the period that the samples ends
                new_samples, self.percent_through_period = sin(self.freq, sample_count=frame_count, fs=FS, volume=self.volume, previous_volume=self.previous_volume, percent_through_period=self.percent_through_period, overtones = MY_OVERTONES)
            else:
                new_samples = np.zeros(frame_count).astype(np.float32)
            samples = np.copy(new_samples)

            ## Increment the buffer counter whenever we are playing or recording
            self.loop_buffer_index += 1
            self.loop_buffer_index %= self.metronome.measure_len

            ## If playing loops, then add all the unmuted loops to the samples
            if self.loop_playing:
                for loop in self.loops:
                    if not loop.muted:
                        samples += loop.volume * loop.buffers[self.loop_buffer_index]

            ## Save the new samples to the active loop
            if self.loop_recording:
                if self.volume > 0:
                    assert len(self.active_loops) == 1
                    active_loop = self.loops[self.active_loops[0]]
                    active_loop.buffers[self.loop_buffer_index] += new_samples
                    active_loop.add_recorded_note(self.loop_buffer_index, self.music_maker.pitch, self.volume, self.previous_volume, self.music_maker.scale)
                    active_loop.has_recorded = True
            
            ## Generate metronome ticks
            if self.music_maker.metronome.is_beat(self.loop_buffer_index) and self.music_maker.metronome.sound:
                samples += np.random.rand(frame_count).astype(np.float32) * VOLUME * METRONOME_RELATIVE_VOLUME

            self.previous_volume = self.volume

            return (samples, self.callback_flag)

        except pygame.error:
            print("Aborting...")
            return (None, pa.paAbort)

    def do_action(self, action):
        if action == ACTION_START_LOOP_REC and not self.loop_recording:
            self.loop_recording = True
            self.active_loops = [self.active_loops[0]]
            if self.active_loops[0] < 0 or self.loops[self.active_loops[0]].has_recorded:
                self.loops.insert(self.active_loops[0]+1, Loop(self.metronome.measure_len))
                self.active_loops = [self.active_loops[0]+1]
        elif action == ACTION_STOP_LOOP_REC:
            assert len(self.active_loops) == 1
            self.loop_recording = False
            ## Manually request an image update so the background can change properly
            self.loops[self.active_loops[0]].image_needs_update = True

        elif action == ACTION_START_LOOP_PLAY and not self.loop_playing:
            self.loop_playing = True
        elif action == ACTION_STOP_LOOP_PLAY:
            self.loop_playing = False

    def settle_to_volume(self):
        self.volume = (self.volume + self.adjusted_base_volume() * ARTICULATION_DECAY) / (ARTICULATION_DECAY + 1)

    def volume_decay(self):
        self.playing=False
        self.volume *= REVERB
        if self.volume < CUTOFF:
            self.volume = 0

    def adjusted_base_volume(self):
        return loud_to_volume(self.base_volume, self.freq)

    def get_loudness(self):
        return volume_to_loud(self.volume, self.freq)

    def articulate(self):
        self.volume = ARTICULATION_FACTOR * self.adjusted_base_volume()
        self.previous_volume = self.volume
        self.playing=True

    def increase_volume(self):
        self.base_volume *= 1.1

    def decrease_volume(self):
        self.base_volume *= .9

    ## Duplicate the metronome and active loops
    def multiply_tracks(self, num):
        num = int(num)
        prior_length = self.metronome.measure_len
        added = prior_length * (num-1)
        self.metronome.change_measure_length(added)
        self.metronome.change_beat_count(self.metronome.beats * (num-1))
        new_loops = []
        for i,loop in enumerate(self.loops):
            loop.buffers.extend([np.zeros(BUFFER_SIZE).astype(np.float32) for i in range(added)])
            loop.recorded_notes.extend([[] for i in range(added)])
            loop.image_needs_update = True
            if i in self.active_loops:
                for c in range(num-1):
                    loop_copy = loop.get_copy()
                    loop_copy.horizontal_shift((c+1)*prior_length)
                    new_loops.append(loop_copy)
        for l in new_loops:
            self.loops.append(l)
        if len(new_loops) > 0:
            self.active_loops = [i+len(self.active_loops) for i in self.active_loops]




        pass

    def write_loops(self, filename, frame_rate=44100, sample_width=4, volume_adjustment=.8):
        ## Filter out loops which haven't been used
        save_loops = list(filter(lambda l: l.has_recorded, self.loops))
        ## Concatenate buffers within each loop
        loop_samples = [np.concatenate(loop.buffers)*loop.volume for loop in save_loops]
        ## Put the samples together in the way the channels will be saved
        samples = AudioPlayer.interleave_samples(loop_samples)
        ## Default volume adjustment
        samples *= volume_adjustment

        #Prevent clipping
        samples_max = max(abs(x) for x in samples)
        if samples_max * 1.1 > 1:
            samples /= samples_max * 1.1

        #Convert to int32
        samples = (samples * 2**31).astype(np.int32)
        channels = len(save_loops)
        with wave.open(filename, 'wb') as writer:
            writer.setnchannels(channels)
            writer.setframerate(frame_rate)
            writer.setsampwidth(sample_width)
            writer.setnframes(len(samples)//channels)
            writer.writeframes(samples)

    def interleave_samples(sample_channels):
        s1 = sample_channels[0]
        ss = np.empty((s1.size * len(sample_channels),), dtype=s1.dtype)
        for i in range(0,len(sample_channels)):
            ss[i::len(sample_channels)] = sample_channels[i]
        return ss
