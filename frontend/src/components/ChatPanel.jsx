import { useState, useEffect, useRef } from 'react';
import { Send, Square } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

export function ChatPanel({
  onMessage,
  onSend,
  onStop,
  status,
  currentSessionId,
  initialMessages = [],
  onToggleSmartPanel,
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
    const handler = (event) => {
      switch (event.type) {
        case 'transcript':
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
        case 'token': {
          const chunk = event.payload.text;
          setStreamingText((prev) => prev + chunk);
          streamingRef.current += chunk;
          break;
        }
        case 'response_done': {
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
        }
        default:
          break;
      }
    };
    const unsubscribe = onMessage(handler);
    return () => {
      try {
        unsubscribe();
      } catch {}
    };
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

  const hasMessages = messages.length > 0 || isStreaming;

  return (
    <div className="flex flex-col h-full min-w-0">
      {/* Chat header bar */}
      <div className="flex items-center justify-end px-4 py-2 border-b border-white/[0.06]">
        <button
          type="button"
          onClick={onToggleSmartPanel}
          className="p-1.5 rounded-lg hover:bg-white/[0.06] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
          title="Toggle activity panel"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
            <rect x="1" y="2" width="14" height="12" rx="2" stroke="currentColor" strokeWidth="1.2" />
            <line x1="10" y1="2" x2="10" y2="14" stroke="currentColor" strokeWidth="1.2" />
          </svg>
        </button>
      </div>

      {/* Messages or empty state */}
      {!hasMessages ? (
        <div className="flex-1 flex flex-col items-center justify-center px-6">
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
            className="text-center"
          >
            <p className="text-xl font-medium bg-gradient-to-r from-[var(--accent)] to-violet-400 bg-clip-text text-transparent">
              What can I help you with?
            </p>
            <p className="text-sm text-[var(--text-muted)] mt-2">Ask me anything — I'm always listening.</p>
          </motion.div>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
          <AnimatePresence initial={false}>
            {messages.map((msg, i) => (
              <motion.div
                key={`${currentSessionId}-${i}`}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25 }}
                className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                {msg.role === 'user' ? (
                  /* User: glass bubble with violet left accent */
                  <div className="max-w-[72%] rounded-2xl rounded-br-sm px-4 py-3 text-[13px] leading-relaxed border-l-2 border-[var(--accent)] bg-[var(--surface-elevated)] border border-[var(--glass-border)] shadow-[0_4px_16px_rgba(0,0,0,0.3)]">
                    <p className="whitespace-pre-wrap text-[var(--text-primary)]">{msg.content}</p>
                  </div>
                ) : (
                  /* Assistant: Apple Intelligence style — text directly on surface */
                  <div className="max-w-[80%] py-1">
                    <p className="text-[13px] leading-relaxed text-[var(--text-primary)] text-balance whitespace-pre-wrap">{msg.content}</p>
                  </div>
                )}
              </motion.div>
            ))}
          </AnimatePresence>

          {/* Streaming assistant message */}
          {isStreaming && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex justify-start"
            >
              <div className="max-w-[80%] py-1">
                {streamingText ? (
                  <p className="text-[13px] leading-relaxed text-[var(--text-primary)] text-balance whitespace-pre-wrap">{streamingText}</p>
                ) : (
                  <div className="flex gap-1.5 items-center py-2">
                    <div className="w-1.5 h-1.5 bg-[var(--accent)]/50 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                    <div className="w-1.5 h-1.5 bg-[var(--accent)]/50 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                    <div className="w-1.5 h-1.5 bg-[var(--accent)]/50 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                  </div>
                )}
              </div>
            </motion.div>
          )}

          <div ref={messagesEndRef} />
        </div>
      )}

      {/* Input dock */}
      <motion.form
        initial={{ y: 20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.4, delay: 0.2, ease: [0.25, 0.1, 0.25, 1] }}
        onSubmit={handleSubmit}
        className="px-4 pb-4 pt-2"
      >
        <div className="flex items-end gap-2 rounded-2xl border border-[var(--glass-border)] bg-[var(--glass-bg)] backdrop-blur-xl p-2 shadow-[0_-4px_24px_rgba(0,0,0,0.15)]">
          <textarea
            ref={inputRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSubmit(e);
              }
            }}
            placeholder="Send a message..."
            className="flex-1 bg-transparent border-none outline-none resize-none text-[13px] text-[var(--text-primary)] placeholder-[var(--text-secondary)] px-3 py-2 max-h-32"
          />
          <div className="flex items-center gap-1 shrink-0">
            {isBusy ? (
              <motion.button
                type="button"
                onClick={onStop}
                whileHover={{ scale: 1.1 }}
                whileTap={{ scale: 0.9 }}
                animate={{ boxShadow: ['0 0 0 0 rgba(239,68,68,0)', '0 0 0 8px rgba(239,68,68,0.1)', '0 0 0 0 rgba(239,68,68,0)'] }}
                transition={{ boxShadow: { duration: 1.5, repeat: Infinity }, default: { duration: 0.15 } }}
                className="p-2.5 rounded-xl border-2 border-red-500/40 text-red-400"
                title="Stop generation"
              >
                <Square size={13} fill="currentColor" />
              </motion.button>
            ) : (
              <motion.button
                type="submit"
                disabled={!input.trim() || !currentSessionId}
                whileHover={{ scale: 1.05, boxShadow: '0 0 20px rgba(167,139,250,0.3)' }}
                whileTap={{ scale: 0.92 }}
                className="p-2.5 rounded-xl bg-[var(--accent)] text-white disabled:opacity-30 disabled:hover:shadow-none transition-all duration-200"
              >
                <Send size={14} />
              </motion.button>
            )}
          </div>
        </div>
      </motion.form>
    </div>
  );
}
