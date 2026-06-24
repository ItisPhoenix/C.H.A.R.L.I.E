import { useState, useEffect, useRef } from 'react';
import { Send, Square } from 'lucide-react';

export function ChatPanel({
  onMessage,
  onSend,
  onStop,
  status,
  currentSessionId,
  initialMessages = [],
}) {
  const [messages, setMessages] = useState(initialMessages);
  const [input, setInput] = useState('');
  const [streamingText, setStreamingText] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);

  const messagesEndRef = useRef(null);
  const streamingRef = useRef('');
  const inputRef = useRef(null);

  // Sync messages when session changes
  useEffect(() => {
    setMessages(initialMessages);
    setStreamingText('');
    streamingRef.current = '';
    setIsStreaming(false);
  }, [currentSessionId, initialMessages]);

  // WS event subscription
  useEffect(() => {
    const unsubscribe = onMessage((event) => {
      switch (event.type) {
        case 'transcript':
          // Server is the single source of truth for user messages.
          setMessages((prev) => [
            ...prev,
            { role: 'user', content: event.payload.text },
          ]);
          break;
        case 'thinking':
          setIsStreaming(true);
          setStreamingText('');
          streamingRef.current = '';
          break;
        case 'token':
          const chunk = event.payload.text;
          setStreamingText((prev) => prev + chunk);
          streamingRef.current += chunk;
          break;
        case 'response_done':
          setIsStreaming(false);
          const finalContent = streamingRef.current;
          if (finalContent) {
            setMessages((prev) => [
              ...prev,
              { role: 'assistant', content: finalContent },
            ]);
            streamingRef.current = '';
          }
          setStreamingText('');
          break;
        default:
          break;
      }
    });
    return unsubscribe;
  }, [onMessage]);

  // Auto-scroll on message / token updates
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingText]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!input.trim() || !currentSessionId) return;
    onSend({
      type: 'chat',
      text: input.trim(),
      session_id: currentSessionId,
    });
    setInput('');
    inputRef.current?.focus();
  };

  const isBusy = status === 'thinking' || status === 'speaking';

  return (
    <div className="flex flex-col h-full bg-zinc-950">
      {/* Messages List */}
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[72%] rounded-2xl px-4 py-3 text-[13px] leading-relaxed shadow-lg transition-all duration-500 ${
                msg.role === 'user'
                  ? 'bg-indigo-600/90 text-white rounded-br-sm shadow-indigo-900/20'
                  : 'bg-zinc-900/80 text-zinc-200 border border-zinc-800/80 rounded-bl-sm shadow-black/40 backdrop-blur-sm'
              }`}
            >
              <div className="text-[9px] uppercase tracking-[0.15em] text-zinc-500 mb-1 font-semibold">
                {msg.role}
              </div>
              <p className="whitespace-pre-wrap">{msg.content}</p>
            </div>
          </div>
        ))}

        {/* Streaming assistant message */}
        {isStreaming && (
          <div className="flex justify-start">
            <div className="max-w-[72%] rounded-2xl rounded-bl-sm px-4 py-3 text-[13px] leading-relaxed bg-zinc-900/80 text-zinc-200 border border-zinc-800/80 shadow-lg shadow-black/40 backdrop-blur-sm">
              <div className="text-[9px] uppercase tracking-[0.15em] text-zinc-500 mb-1 font-semibold animate-pulse">
                assistant (typing)
              </div>
              {streamingText ? (
                <p className="whitespace-pre-wrap">{streamingText}</p>
              ) : (
                <div className="flex gap-1.5 items-center py-1.5">
                  <div className="w-1.5 h-1.5 bg-zinc-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                  <div className="w-1.5 h-1.5 bg-zinc-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                  <div className="w-1.5 h-1.5 bg-zinc-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                </div>
              )}
            </div>
          </div>
        )}

        {/* Scroll anchor — this is the ONLY element that holds messagesEndRef */}
        <div ref={messagesEndRef} />
      </div>

      {/* Input Form */}
      <form
        onSubmit={handleSubmit}
        className="px-6 py-4 border-t border-zinc-900 bg-zinc-950/90 backdrop-blur-md"
      >
        <div className="relative flex items-center">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Send a message..."
            className="w-full bg-zinc-900 border border-zinc-800 rounded-2xl py-3 pl-5 pr-14 text-[13px] text-white placeholder-zinc-600 focus:outline-none focus:border-indigo-500/60 focus:shadow-[0_0_20px_rgba(99,102,241,0.08)] transition-all duration-500 shadow-inner"
          />
          <div className="absolute right-2 flex items-center gap-1">
            {isBusy ? (
              <button
                type="button"
                onClick={onStop}
                className="p-2.5 bg-red-500/10 hover:bg-red-500/20 text-red-400 rounded-xl transition-all duration-350 active:scale-95"
                title="Stop generation"
              >
                <Square size={13} fill="currentColor" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim() || !currentSessionId}
                className="p-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-30 disabled:hover:bg-indigo-600 text-white rounded-xl transition-all duration-350 active:scale-95 shadow-lg shadow-indigo-900/30"
              >
                <Send size={14} />
              </button>
            )}
          </div>
        </div>
      </form>
    </div>
  );
}
