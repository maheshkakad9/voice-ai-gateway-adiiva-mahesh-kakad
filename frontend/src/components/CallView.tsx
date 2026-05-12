import React, { useState, useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import { Phone, PhoneOff, Activity, Clock, LogOut } from 'lucide-react';
import { VoiceClient } from '../lib/websocket';
import type { WsStatus, ServerEvent } from '../lib/websocket';
import { AudioCapture, AudioPlayer } from '../lib/audio';

interface CallViewProps {
  token: string;
  onLogout: () => void;
}

export const CallView: React.FC<CallViewProps> = ({ token, onLogout }) => {
  const [status, setStatus] = useState<WsStatus>('disconnected');
  const [isActive, setIsActive] = useState(false);
  const [events, setEvents] = useState<ServerEvent[]>([]);
  const [latency, setLatency] = useState<number | null>(null);

  const [debugStats, setDebugStats] = useState({
    micActive: false,
    audioFramesReceived: 0,
    bytesReceived: 0,
    decodeSuccess: 0,
    decodeError: 0,
    playbackEvents: 0
  });

  const clientRef = useRef<VoiceClient | null>(null);
  const captureRef = useRef<AudioCapture | null>(null);
  const playerRef = useRef<AudioPlayer | null>(null);
  const eventsEndRef = useRef<HTMLDivElement>(null);

  // Auto scroll events
  useEffect(() => {
    eventsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events]);

  const handleStartCall = async () => {
    try {
      // 1. Initialize Audio playback
      playerRef.current = new AudioPlayer({
        onDecodeSuccess: () => setDebugStats(prev => ({ ...prev, decodeSuccess: prev.decodeSuccess + 1 })),
        onDecodeError: (err) => {
          console.error("Debug Panel - Decode Error:", err);
          setDebugStats(prev => ({ ...prev, decodeError: prev.decodeError + 1 }));
        },
        onPlayback: () => setDebugStats(prev => ({ ...prev, playbackEvents: prev.playbackEvents + 1 }))
      });

      // Explicitly resume AudioContext right after explicit user interaction
      await playerRef.current.resume();
      
      // 2. Initialize WebSocket
      clientRef.current = new VoiceClient(
        token,
        (newStatus) => setStatus(newStatus),
        (audioData) => {
          const byteLength = audioData instanceof ArrayBuffer ? audioData.byteLength : audioData.size;
          setDebugStats(prev => ({ 
            ...prev, 
            audioFramesReceived: prev.audioFramesReceived + 1,
            bytesReceived: prev.bytesReceived + byteLength
          }));
          playerRef.current?.playChunk(audioData);
        },
        (event) => {
          setEvents((prev) => [...prev, event]);
          if (event.latency) {
            setLatency(event.latency);
          }
        }
      );
      clientRef.current.connect();

      // 3. Initialize Audio Capture
      captureRef.current = new AudioCapture((pcmData) => {
        setDebugStats(prev => ({ ...prev, micActive: true }));
        clientRef.current?.sendAudio(pcmData);
      });
      await captureRef.current.start();
      
      setIsActive(true);
    } catch (err) {
      console.error("Failed to start call:", err);
      handleEndCall();
    }
  };

  const handleEndCall = () => {
    setIsActive(false);
    setStatus('disconnected');
    setDebugStats(prev => ({ ...prev, micActive: false }));
    captureRef.current?.stop();
    playerRef.current?.stop();
    clientRef.current?.disconnect();
    
    captureRef.current = null;
    playerRef.current = null;
    clientRef.current = null;
  };

  useEffect(() => {
    return () => {
      handleEndCall();
    };
  }, []);

  return (
    <div className="w-full h-screen max-w-4xl mx-auto flex flex-col p-6">
      <header className="flex justify-between items-center py-4 border-b border-gray-800">
        <div className="flex items-center gap-3">
          <div className={`w-3 h-3 rounded-full ${status === 'connected' ? 'bg-green-500' : status === 'error' ? 'bg-red-500' : 'bg-gray-500'}`} />
          <span className="font-medium text-gray-200 capitalize">{status}</span>
        </div>
        <div className="flex items-center gap-6 text-sm text-gray-400">
          {latency !== null && (
            <div className="flex items-center gap-2">
              <Activity size={16} />
              <span>{latency}ms</span>
            </div>
          )}
          <button onClick={onLogout} className="flex items-center gap-2 hover:text-white transition-colors">
            <LogOut size={16} />
            <span>Logout</span>
          </button>
        </div>
      </header>

      <main className="flex-1 flex flex-col items-center justify-center relative">
        {/* Dynamic Voice Orb */}
        <div className="relative flex items-center justify-center w-64 h-64 mb-12">
          {isActive && status === 'connected' && (
            <>
              <motion.div
                className="absolute inset-0 rounded-full bg-primary/20 blur-xl"
                animate={{
                  scale: [1, 1.5, 1],
                  opacity: [0.5, 0.8, 0.5],
                }}
                transition={{
                  duration: 2,
                  repeat: Infinity,
                  ease: "easeInOut"
                }}
              />
              <motion.div
                className="absolute inset-4 rounded-full bg-primary/30 blur-md"
                animate={{
                  scale: [1, 1.2, 1],
                  opacity: [0.6, 1, 0.6],
                }}
                transition={{
                  duration: 1.5,
                  repeat: Infinity,
                  ease: "easeInOut",
                  delay: 0.2
                }}
              />
            </>
          )}
          <div className="relative z-10 w-32 h-32 rounded-full bg-gradient-to-tr from-primary to-accent shadow-2xl flex items-center justify-center ring-4 ring-background">
             <Activity size={48} className="text-white opacity-80" />
          </div>
        </div>

        {/* Controls */}
        <div className="flex gap-4">
          {!isActive ? (
            <button
              onClick={handleStartCall}
              className="flex items-center gap-3 px-8 py-4 rounded-full bg-white text-black font-semibold hover:bg-gray-100 transition-transform active:scale-95 shadow-xl"
            >
              <Phone size={24} />
              <span>Start Call</span>
            </button>
          ) : (
            <button
              onClick={handleEndCall}
              className="flex items-center gap-3 px-8 py-4 rounded-full bg-red-500 text-white font-semibold hover:bg-red-600 transition-transform active:scale-95 shadow-xl shadow-red-500/20"
            >
              <PhoneOff size={24} />
              <span>End Call</span>
            </button>
          )}
        </div>
      </main>

      {/* Debug Panel */}
      <div className="absolute top-20 right-6 bg-black/80 backdrop-blur-md border border-gray-700 p-4 rounded-xl text-xs font-mono text-gray-300 shadow-2xl w-72 z-50">
        <h4 className="text-white font-semibold mb-3 pb-2 border-b border-gray-700 flex items-center justify-between">
          <span>Audio Debug Stats</span>
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${debugStats.micActive ? 'bg-green-500 animate-pulse' : 'bg-gray-600'}`} title="Mic Active" />
            <div className={`w-2 h-2 rounded-full ${status === 'connected' ? 'bg-blue-500 animate-pulse' : 'bg-gray-600'}`} title="WS Connected" />
          </div>
        </h4>
        <div className="space-y-2">
          <div className="flex justify-between">
            <span className="text-gray-500">WS Status:</span>
            <span className={status === 'connected' ? 'text-green-400' : 'text-yellow-400'}>{status}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Mic Active:</span>
            <span className={debugStats.micActive ? 'text-green-400' : 'text-red-400'}>{debugStats.micActive ? 'Yes' : 'No'}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Audio Frames Rx:</span>
            <span className="text-blue-400">{debugStats.audioFramesReceived}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Bytes Rx:</span>
            <span className="text-blue-400">{(debugStats.bytesReceived / 1024).toFixed(2)} KB</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Decode Success:</span>
            <span className="text-green-400">{debugStats.decodeSuccess}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Decode Errors:</span>
            <span className={debugStats.decodeError > 0 ? 'text-red-500 font-bold' : 'text-gray-400'}>{debugStats.decodeError}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Playback Events:</span>
            <span className="text-purple-400">{debugStats.playbackEvents}</span>
          </div>
        </div>
      </div>

      {/* Transcript / Events Panel */}
      <footer className="h-64 bg-surface rounded-2xl border border-gray-800 p-4 flex flex-col">
        <h3 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-2">
          <Clock size={16} />
          Session Events
        </h3>
        <div className="flex-1 overflow-y-auto space-y-3 pr-2 font-mono text-sm">
          {events.length === 0 ? (
            <div className="text-gray-600 italic">No events yet...</div>
          ) : (
            events.map((evt, i) => (
              <div key={i} className="p-3 rounded-lg bg-background border border-gray-800 text-gray-300 break-words">
                <span className="text-primary/70 font-semibold mr-2">[{new Date().toLocaleTimeString()}]</span>
                {JSON.stringify(evt)}
              </div>
            ))
          )}
          <div ref={eventsEndRef} />
        </div>
      </footer>
    </div>
  );
};
