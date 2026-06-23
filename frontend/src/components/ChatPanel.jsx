import { useState, useEffect, useRef } from 'react';
import { Send, Square } from 'lucide-react';

export function ChatPanel({ onMessage, onSend, onStop, status }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [streamingText, setStreamingText] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const messagesEndRef = useRef(null);

  // Track accumulated streaming text synchronously
  const streamingRef = useRef('');

  useEffect(() => {
    window.__received_events = [];
    const unsubscribe = onMessage((event) => {
      window.__received_events.push(event);
      switch (event.type) {
        case 'transcript':
          // Avoid duplicate user messages in UI
          setMessages((prev) => {
            if (prev.length > 0 && prev[prev.length - 1].role === 'user' && prev[prev.length - 1].content === event.payload.text) {
              return prev;
            }
            return [
              ...prev,
              { role: 'user', content: event.payload.text },
            ];
          });
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

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingText]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!input.trim()) return;
    onSend({ type: 'chat', text: input.trim() });
    setMessages((prev) => [...prev, { role: 'user', content: input.trim() }]);
    setInput('');
  };

  const isBusy = status === 'thinking' || status === 'speaking';

  return (
    <div className="flex flex-col h-full bg-gray-900">
      {/* Messages List */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[70%] rounded-lg p-3 text-sm ${
                msg.role === 'user'
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-800 text-gray-200 border border-gray-700'
              }`}
            >
              <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1 font-semibold">
                {msg.role}
              </div>
              <p className="whitespace-pre-wrap">{msg.content}</p>
            </div>
          </div>
        ))}

        {/* Streaming message */}
        {isStreaming && (
          <div className="flex justify-start">
            <div className="max-w-[70%] rounded-lg p-3 text-sm bg-gray-800 text-gray-200 border border-gray-700">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1 font-semibold animate-pulse">
                assistant (typing)
              </div>
              {streamingText ? (
                <p className="whitespace-pre-wrap">{streamingText}</p>
              ) : (
                <div className="flex gap-1 items-center py-1">
                  <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                  <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                  <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                </div>
              )}
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input Form */}
      <form onSubmit={handleSubmit} className="p-4 border-t border-gray-700 bg-gray-900">
        <div className="relative flex items-center">
          <input
            type="text"
            ref={messagesEndRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Type a message..."
            className="w-full bg-gray-800 border border-gray-700 rounded-lg py-2.5 pl-4 pr-12 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-indigo-500 transition-colors"
          />
          <div className="absolute right-2 flex items-center gap-1.5">
            {isBusy ? (
              <button
                type="button"
                onClick={onStop}
                className="p-1.5 bg-red-600/20 hover:bg-red-600/30 text-red-400 rounded-md transition-colors"
                title="Stop generation"
              >
                <Square size={14} fill="currentColor" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                className="p-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:hover:bg-indigo-600 text-white rounded-md transition-colors"
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
